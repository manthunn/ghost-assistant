# Tried and shelved — things blocked by hardware, not by design

Notes on work that was built and measured but **not kept**, so a future upgrade can
pick it straight back up instead of rediscovering all of this. Nothing here is a
dead end in principle; each was blocked by a specific, documented limit.

Hardware this was measured on: **RTX 3060 Laptop, 6 GB VRAM**, Windows 11,
shared with Vivaldi + VS Code + Spotify (all GPU-accelerated).

---

## 1. Custom / cloned voice for Ghost (Voicebox)

**Goal:** replace Gemini's built-in voice with a cloned "Batman" voice.

**Verdict: shelved.** Works, sounds right, but no local engine was fast enough for
conversation on 6 GB VRAM. Gemini's `Rasalgethi` kept instead.

### Measured, same hardware, same reference sample

| Engine | Size | Cold | Warm (short reply) | Briefing-length | Clones? |
| --- | --- | --- | --- | --- | --- |
| Kokoro 82M | 314 MB | 14.6 s | **0.5 s** | 0.9 s | ❌ presets only |
| Qwen TTS 1.7B | 4.3 GB | 33 s | **72.8 s** | 96.9 s | ✅ |
| Qwen TTS 0.6B | 2.4 GB | 23.3 s | **27.1 s** | 58.1 s | ✅ |
| chatterbox-turbo | 3.9 GB | 44.1 s | **5.1 s** | 7.1 s | ✅ |
| Gemini Live (kept) | n/a — cloud | — | **~1.3 s to first audio, streams** | streams | ❌ |

### Why each was rejected

- **Kokoro** is fast enough but **cannot clone** — fixed preset voices only. It has 8
  British voices (`bm_george`, `bm_daniel`, `bm_lewis`, `bm_fable`) which get a
  decent "composed British assistant", and profiles accept a `personality` prompt.
  Best option if you ever want local voice *without* cloning.
- **Qwen TTS (both sizes)** never cached — warm was no faster than cold, meaning the
  model reloads per request. 0.6B is the floor; there is nothing smaller in the
  family (`qwen3-0.6b/1.7b/4b` are text LLMs for transcript cleanup, not TTS).
- **chatterbox-turbo** was the only local engine that genuinely worked: 5.1 s warm,
  and warm ≪ cold so it *does* stay resident. Still 5 s of silence per reply, and it
  holds **4436 MB of 6144 MB VRAM** while loaded — which was the dealbreaker, since
  smooth Vivaldi/VS Code performance mattered more than the voice.

### Revisit when

A GPU with **≥12 GB VRAM**. chatterbox-turbo at 5.1 s was memory-starved, not
compute-bound (GPU utilisation sat at 38% while VRAM was at 94%). With real headroom
it should drop well under a second, at which point live cloned voice becomes viable.

### The Batman voice is archived, not lost

`C:\Users\ranem\OneDrive\Documents\ghost-voice-archive\`

- `batman_voice_profile.zip` — Voicebox's own export format, re-importable
- `batman_reference_sample.wav` — the 6.36 s reference clip
- `batman_reference_text.txt` — the **corrected** transcript
- `batman_profile_meta.json` — profile settings

Deliberately **not committed to git**: the reference clip is cloned from copyrighted
video, so it stays local rather than being republished in a public repo.

### Bug worth remembering: phantom word at the start of every generation

The cloned voice prefixed a stray **"this"** onto every single output
("thisghost online"). Cause was **not** the model — Voicebox's auto-transcription
misheard the sample's final word `it` as `this`. Zero-shot cloning conditions on
(reference audio + reference transcript); because the transcript asserted a word the
audio didn't contain, the model faithfully generated the phantom word every time.

Fix: transcribe the reference with `faster-whisper` using `word_timestamps=True` and
set the reference text to what the audio *actually* says. Adding punctuation alone
does nothing — the words have to match.

**Lesson:** always verify an auto-generated reference transcript before blaming the
TTS model.

---

## 2. Voicebox API notes (if it's ever reinstalled)

Local FastAPI server on `http://127.0.0.1:17493`, full OpenAPI at `/openapi.json`.

- Synthesis is **async**: `POST /generate` returns an id, then poll
  **`GET /history/{id}`** for `status == "completed"`, then `GET /audio/{id}`.
  Do *not* poll `GET /generate/{id}/status` expecting JSON — it's an SSE stream
  (`data: {...}`), which will blow up a plain `.json()` call.
- `POST /speak` plays on the desktop itself; `/generate` + `/audio` is what you want
  if the caller needs control over playback and interruption.
- Create a preset-voice profile with `voice_type: "preset"` +
  `preset_engine` + `preset_voice_id`; profiles also take a free-text `personality`.
- Effects are a per-request `effects_chain` (or per-profile): `pitch_shift`,
  `compressor`, `lowpass`, `highpass`, `reverb`, `delay`, `chorus`, `gain`. A
  Batman-ish voice is achievable **without cloning** via a deep British preset plus
  pitch-shift −3..−5, heavy compression, lowpass ~5–7 kHz and light reverb.
- `POST /models/download/cancel` requires a JSON body (`{"model_name": ...}`);
  without one it 422s while the download keeps running.
- **`POST /shutdown` does not auto-restart the server** — no watchdog picks it up.
  Restart the desktop app instead. `POST /models/load` crashed the server outright
  at least once.
- The CUDA backend can't be deleted while active ("Switch to CPU first"); removing
  `%APPDATA%\sh.voicebox.app` gets rid of it along with everything else.
- Models land in `~/.cache/huggingface/hub`, **not** in the app folder — so app-folder
  size badly understates real disk use.

---

## 3. Gemini Live API constraints found the hard way

- **`gemini-3.1-flash-live-preview` is audio-out only.** `response_modalities: ["TEXT"]`
  is rejected outright. This kills the otherwise-obvious "emit text, synthesise
  locally, save money" plan: you'd still pay for audio output you then discard, so
  routing through a local TTS has **no cost saving**, only a voice change.
- `session.receive()` yields **exactly one turn** then ends — it must be re-entered
  per turn or the assistant goes permanently deaf after its first reply.
- Screen frames must be sent as `video=types.Blob(data=jpeg, mime_type="image/jpeg")`.
  Passing a raw PIL image **silently fails** — the model just says it can't see the
  screen. `media=` is deprecated and closes the socket with a 1007.
- Without an explicit `speech_config` the voice **drifts between sessions**.

---

## 4. Still open (not hardware-blocked)

- **Scheduled/background runs.** The briefing and YouTube checks are on-demand or
  fire at startup after a 6 h gap. Nothing is actually cron'd — would need Windows
  Task Scheduler or a resident loop.
- **Email sending.** Compose-only by design: new Outlook (`olk.exe`) has no COM and
  no Graph credentials here, so programmatic send isn't possible; the user presses
  Send. Worth revisiting only via Microsoft Graph.
- **Cross-AI delegation** to other LLM APIs.
