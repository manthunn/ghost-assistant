# Tried and shelved

Notes on work that was built and measured but **not kept**, so a future attempt can
pick it straight back up instead of rediscovering all of this. Each was blocked by a
specific, documented limit — mostly hardware, once by an upstream system's behaviour.

Hardware the voice work was measured on: **RTX 3060 Laptop, 6 GB VRAM**, Windows 11,
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

## 4. Google Calendar via "secret address in iCal format"

**Goal:** read the Monash timetable, assignment and exam calendars without OAuth.

**Verdict: shelved, permanently.** Replaced by the Calendar API + OAuth
(`ghost/skills/calendar_feed.py`, `setup_gcal.py`). Do not go back to iCal links.

Each Google Calendar exposes a private iCal URL under *Settings → that calendar →
Integrate calendar → Secret address in iCal format*. It was genuinely attractive:
no OAuth, no credentials file, no API to enable, read-only by construction, and the
URLs sit in gitignored `.env`. It worked for about a day at a time.

### Why it failed

The feeds returned **404 within roughly 24 hours, every time**, and re-pasting fresh
links bought another day at most. The obvious diagnosis — that the secret token had
been Reset — was **wrong**, and chasing it wasted several rounds of re-pasting.

The real cause: the **calendar IDs themselves kept changing**.

```
c_efdb0f46…  →  c_16b7ec89…  →  dead
```

Monash's timetable/Moodle → Google sync **deletes and recreates** the calendars
rather than updating them in place. A secret iCal URL is bound to one calendar ID
for its lifetime, so when the calendar behind it is deleted the link dies with it.
There is no configuration on our side that survives this — the identifier the link
depends on is not stable, and never will be.

### Why the API fixes it properly

`calendarList().list()` resolves calendars **by name** at request time — "Classes",
"Assignments and quizzes", "Final assessments". A recreated calendar keeps its name
and gets a new ID, so the next call finds it with zero reconfiguration. The only
thing that can now break the lookup is an actual **rename**, which is handled by
`GCAL_CLASSES_NAME` / `GCAL_ASSIGNMENTS_NAME` / `GCAL_FINALS_NAME` in `.env`.

`calendar_feed.py` retries once against a freshly-resolved ID when a read 404s
mid-call, which covers the calendar being recreated between the lookup and the read.

### Two things worth keeping from the iCal version

- **Timezones.** Feed timestamps are UTC/offset-aware; rendering them without
  `.astimezone()` made a 10 am class read as "12:00 AM" and an 11:55 pm deadline as
  "1:55 PM". Sort keys also have to be normalised — comparing aware datetimes,
  naive datetimes and all-day `date`s directly raises `TypeError`. Both carried over.
- **Recurring events.** The iCal path needed `recurring_ical_events`, because a naive
  parser reports a weekly class's *original* occurrence, not this week's. The API
  does this server-side with `singleEvents=True`, so `icalendar` and
  `recurring-ical-events` were both dropped from `requirements.txt`.

### OAuth gotchas, both hit during the real setup on 2026-08-04

**1. In Testing mode, the project owner is still blocked.** Running
`setup_gcal.py` against a Testing-status app fails with:

```
Access blocked: Ghost has not completed the Google verification process
Error 403: access_denied
```

Being the developer, the project owner and the account that created the OAuth
client grants you *nothing* — Testing mode only admits accounts explicitly listed
under **Audience → Test users**. The fix is to publish, not to add yourself.

**2. Publish to "In production", not "Testing".** Testing expires refresh tokens
after **7 days**, which would reproduce the original symptom — calendar silently
stops working after a few days — for a completely unrelated reason.
`calendar_feed.py` names this explicitly in its refresh-failure message so the
diagnosis isn't lost next time.

Publishing an unverified personal app to production is fine and is what this
project does. Consequences, both harmless here:

- Consent shows a **"Google hasn't verified this app"** interstitial — proceed via
  *Advanced → Go to Ghost (unsafe)*. Distinct from the hard 403 above: this one is
  clickable, that one isn't.
- A **100-user lifetime cap** applies to unverified apps requesting sensitive
  scopes, and it cannot be reset. Irrelevant for a single-user assistant.
- Google's own verification path is documented as taking 4–6 weeks. Not worth
  starting for an app with exactly one user.

### Verified working

`setup_gcal.py` completed on 2026-08-04. All three calendars resolved by name on
the first live call, returning real ECE2072 / ECE2191 / ECE2111 timetable and
deadline data with correct Melbourne times — a 10 am lab renders as `10:00 AM` and
an 11:55 pm deadline as `11:55 PM`, confirming both timezone bugs are dead against
live data and not just fixtures. Token carries a refresh token and the
`calendar.readonly` scope only.

---

## 5. Still open (not hardware-blocked)

- **Scheduled/background runs.** The briefing and YouTube checks are on-demand or
  fire at startup after a 6 h gap. Nothing is actually cron'd — would need Windows
  Task Scheduler or a resident loop.
- **Email sending.** Compose-only by design: new Outlook (`olk.exe`) has no COM and
  no Graph credentials here, so programmatic send isn't possible; the user presses
  Send. Worth revisiting only via Microsoft Graph.
- **Cross-AI delegation** to other LLM APIs.
