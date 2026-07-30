import asyncio
import threading
import queue
import sounddevice as sd
from google.genai import types
from .brain import client, SYSTEM
from .skills import TOOLS, FUNCTIONS

MODEL = "gemini-3.1-flash-live-preview"
IN_RATE = 16000
OUT_RATE = 24000
BLOCK = 1600  # 100ms of audio at 16kHz

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

    def stop(self):
        self.stream.stop()
        self.stream.close()

async def _mic_loop(session, mic_q, stop_event):
    while not stop_event.is_set():
        chunk = await asyncio.to_thread(mic_q.get)
        if chunk is None:
            break
        await session.send_realtime_input(
            audio=types.Blob(data=chunk, mime_type=f"audio/pcm;rate={IN_RATE}"))

async def run(ui, stop_event):
    gemini_tools = [types.Tool(function_declarations=[t["function"] for t in TOOLS])]
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=SYSTEM,
        tools=gemini_tools,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    player = AudioPlayer()
    mic_q = queue.Queue()
    speaking = threading.Event()  # muted while set, to avoid the speaker->mic feedback loop

    def mic_callback(indata, frames, time_info, status):
        if speaking.is_set():
            return
        mic_q.put_nowait(bytes(indata))

    mic_stream = sd.RawInputStream(samplerate=IN_RATE, channels=1, dtype="int16",
                                    blocksize=BLOCK, callback=mic_callback)

    async with client.aio.live.connect(model=MODEL, config=config) as session:
        mic_stream.start()
        ui.set("🟢 Listening")
        mic_task = asyncio.create_task(_mic_loop(session, mic_q, stop_event))
        ghost_line_open = False
        try:
            async for response in session.receive():
                if stop_event.is_set():
                    break

                sc = response.server_content
                if sc:
                    if sc.interrupted:
                        player.clear()
                        speaking.clear()
                    if sc.input_transcription and sc.input_transcription.text:
                        print(f"\nYou: {sc.input_transcription.text}")
                    if sc.model_turn:
                        speaking.set()
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
                        speaking.clear()
                        ui.set("🟢 Listening")

                if response.tool_call:
                    speaking.clear()
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
                        break
                    ui.set("🟢 Listening")
        finally:
            mic_task.cancel()
            mic_stream.stop()
            mic_stream.close()
            player.stop()
