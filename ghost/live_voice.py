import asyncio
import threading
import queue
import time
import sounddevice as sd
from google.genai import types
from .brain import client, SYSTEM
from .skills import TOOLS, FUNCTIONS
from .skills.briefing import should_brief

MODEL = "gemini-3.1-flash-live-preview"
# Pinned so Ghost sounds identical every session - without an explicit
# speech_config the Live API picks its own voice and it drifts between runs.
# Other steady/assistant-ish options: Charon (informative),
# Sadaltager (knowledgeable), Iapetus (clear), Alnilam or Orus (firm),
# Schedar (even), Sulafat (warm). Full list: 30 prebuilt voices.
VOICE = "Rasalgethi"
IN_RATE = 16000
OUT_RATE = 24000
BLOCK = 1600  # 100ms of audio at 16kHz
IDLE_TIMEOUT = 300  # end the session after 5 min with no user speech
IDLE_CHECK = 5      # how often the watchdog checks

class AudioPlayer:
    """Streams PCM16 chunks out as they arrive, so playback stays gapless."""
    def __init__(self, rate=OUT_RATE):
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.stream = sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16",
                                          blocksize=1200, callback=self._callback)
        self.stream.start()

    def _callback(self, outdata, frames, time_info, status):
        needed = frames * 2  # int16 mono = 2 bytes/frame
        with self.lock:
            chunk = bytes(self.buf[:needed])
            del self.buf[:len(chunk)]
        if len(chunk) < needed:
            chunk += b"\x00" * (needed - len(chunk))
        outdata[:] = chunk

    def feed(self, pcm_bytes):
        with self.lock:
            self.buf.extend(pcm_bytes)

    def clear(self):
        with self.lock:
            self.buf.clear()

    def is_active(self):
        with self.lock:
            return len(self.buf) > 0

    def stop(self):
        self.stream.stop()
        self.stream.close()

async def _mic_loop(session, mic_q, stop_event):
    while not stop_event.is_set():
        chunk = await asyncio.to_thread(mic_q.get)
        if chunk is None:
            break
        try:
            await session.send_realtime_input(
                audio=types.Blob(data=chunk, mime_type=f"audio/pcm;rate={IN_RATE}"))
        except Exception as e:
            print(f"  [mic send error, continuing] {e}")

async def _idle_watchdog(activity, stop_event):
    """Ends the session once the user has been silent for IDLE_TIMEOUT."""
    while not stop_event.is_set():
        await asyncio.sleep(IDLE_CHECK)
        if time.monotonic() - activity["last"] >= IDLE_TIMEOUT:
            span = (f"{IDLE_TIMEOUT // 60} min" if IDLE_TIMEOUT >= 60
                    else f"{int(IDLE_TIMEOUT)}s")
            print(f"\n[no speech for {span} - ending session]")
            stop_event.set()
            return

async def _receive_loop(session, ui, player, stop_event, activity):
    ghost_line_open = False
    # session.receive() yields exactly ONE model turn then ends (it breaks on
    # turn_complete internally), so it must be re-entered per turn - otherwise
    # the loop exits after the first reply and Ghost goes deaf.
    while not stop_event.is_set():
        async for response in session.receive():
            if stop_event.is_set():
                return

            sc = response.server_content
            if sc:
                if sc.interrupted:
                    player.clear()
                if sc.input_transcription and sc.input_transcription.text:
                    activity["last"] = time.monotonic()
                    print(f"\nYou: {sc.input_transcription.text}")
                if sc.model_turn:
                    ui.set("🟣 Speaking")
                    for part in sc.model_turn.parts:
                        if part.inline_data:
                            player.feed(part.inline_data.data)
                if sc.output_transcription and sc.output_transcription.text:
                    if not ghost_line_open:
                        print("Ghost: ", end="", flush=True)
                        ghost_line_open = True
                    print(sc.output_transcription.text, end="", flush=True)
                if sc.turn_complete:
                    print()
                    ghost_line_open = False
                    ui.set("🟢 Listening")

            if response.tool_call:
                ui.set("🔵 Working")
                function_responses = []
                should_stop = False
                for fc in response.tool_call.function_calls:
                    print(f"  ⚙️ {fc.name}({fc.args})")
                    if fc.name == "end_conversation":
                        should_stop = True
                    try:
                        result = str(FUNCTIONS[fc.name](**(fc.args or {})))
                    except Exception as e:
                        result = f"Tool error: {e}"
                    function_responses.append(types.FunctionResponse(
                        name=fc.name, id=fc.id, response={"result": result[:4000]}))
                await session.send_tool_response(function_responses=function_responses)
                if should_stop:
                    stop_event.set()
                    return
                ui.set("🟢 Listening")

async def run(ui, stop_event):
    gemini_tools = [types.Tool(function_declarations=[t["function"] for t in TOOLS])]
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=SYSTEM,
        tools=gemini_tools,
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE))),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    player = AudioPlayer()
    mic_q = queue.Queue()
    activity = {"last": time.monotonic()}

    def mic_callback(indata, frames, time_info, status):
        # Muted whenever there's still unplayed Ghost audio queued, to avoid the
        # speaker->mic feedback loop. Tied to actual playback state (not a server
        # event) so it can't get stuck muted if a turn-completion signal is missed.
        if player.is_active():
            return
        mic_q.put_nowait(bytes(indata))

    mic_stream = sd.RawInputStream(samplerate=IN_RATE, channels=1, dtype="int16",
                                    blocksize=BLOCK, callback=mic_callback)

    async with client.aio.live.connect(model=MODEL, config=config) as session:
        mic_stream.start()
        ui.set("🟢 Listening")
        # Run mic, receive and idle-watchdog concurrently: the receive loop blocks
        # awaiting server messages, so the watchdog needs to be its own task to be
        # able to end an idle session at all.
        tasks = [
            asyncio.create_task(_mic_loop(session, mic_q, stop_event)),
            asyncio.create_task(_receive_loop(session, ui, player, stop_event, activity)),
            asyncio.create_task(_idle_watchdog(activity, stop_event)),
        ]
        # First session of the day (or 6h+ since the last): open with the briefing
        # unprompted, rather than waiting to be asked.
        if should_brief():
            print("[first session in a while - delivering daily briefing]")
            ui.set("🔵 Working", "daily briefing")
            await session.send_realtime_input(
                text="Give me my daily briefing for today.")
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if not t.cancelled() and t.exception():
                    print(f"  [session ended on error] {t.exception()}")
        finally:
            stop_event.set()
            for t in tasks:
                t.cancel()
            mic_q.put_nowait(None)  # unblock the mic thread's blocking get()
            mic_stream.stop()
            mic_stream.close()
            player.stop()
