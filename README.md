# Ghost — a voice assistant that actually touches the desktop

A hands-free assistant for Windows. You talk to it, it talks back, and in between
it reads your calendar and inbox, drives applications through UI Automation,
searches the web, and remembers things between sessions. 60 tools across 22 skill
modules, auto-discovered at boot.

Built from scratch in Python.

## Architecture: cloud for conversation, local for control

Ghost started as a fully local build — Llama 3.1 through Ollama, faster-whisper
in, offline TTS out. That was measured and abandoned. The reasons are documented
in [`docs/TRIED_AND_SHELVED.md`](docs/TRIED_AND_SHELVED.md) rather than quietly
dropped, because the numbers are the interesting part: on a 6 GB laptop GPU, the
only local TTS that stayed resident held 4.4 GB of VRAM and still took 5 seconds
to start speaking. Gemini Live streams first audio in about 1.3 s.

What it settled into is a deliberate split:

- **Conversation is cloud.** `gemini-3.1-flash-live-preview` over the Live API —
  a single bidirectional socket doing speech-in, reasoning, tool-calling and
  speech-out natively. No separate STT/TTS stages to synchronise.
- **Waking is local.** [`wake.py`](wake.py) runs faster-whisper over short
  rolling windows listening for "hey ghost", and analyses the same raw samples
  for a double clap — signal analysis only, so claps never reach the model and
  cost nothing. This is the whole point of keeping it local: the Live API bills
  per audio-minute, so idle listening on a cloud session would cost money doing
  nothing. Ghost only opens a session — and only starts costing anything — once
  it is actually woken. Arm it at login with `py install_startup.py --wake`.
- **Control is local.** Skills drive Outlook, Spotify, the browser and the
  desktop through pywinauto/UIA and native APIs. Nothing about that needs a model.

An idle-timeout closes the session after 5 minutes of silence, for the same
billing reason.

## What it can do

| Area | Skills |
| --- | --- |
| Desktop | `system_control`, `ui_automation`, `vision`, `screenshot`, `session_control` |
| Comms | `outlook` (read mail, compose drafts), `whatsapp` (read, send, call), `notion` |
| Time | `calendar_feed` (Google Calendar API), `moodle` (Moodle calendar + dashboard), `briefing`, `timers`, `todo` |
| Information | `browser`, `ft_news`, `youtube`, `ai_chats` |
| Core | `memory`, `agents`, `media` |

Some things worth calling out:

- **Calendar resolves by name, not ID.** Monash deletes and recreates the
  timetable calendars, so any fixed calendar ID dies within a day. Looking them
  up by name at request time survives that.
- **Reads Claude and ChatGPT desktop history.** Both are Electron apps, so the
  same UIA path that reads Outlook reads their conversation sidebars.
- **Timers outlive Ghost.** Ghost closes itself after five minutes of silence, so
  an in-process timer would die with it — exactly the case a timer exists for.
  Each one is a detached process instead.
- **Sending is gated.** WhatsApp messages and calls reach another person the
  instant they fire, so they refuse unless the recipient and text have been read
  back and confirmed out loud.
- **Refuses to guess.** Skills that can't see something say so — a collapsed
  Outlook mailbox, a login-gated page, a calendar that moved — rather than
  reporting an absence they can't actually verify.

## Project structure

```
ghost-assistant/
├── main.py              # entry point: UI on the main thread, assistant loop on a daemon thread
├── wake.py              # local listener: "hey ghost" + double clap
├── hotkey.py            # F12 launcher, as an alternative to waking by voice
├── install_startup.py   # arm the listeners at every Windows login
├── clap_calibrate.py    # measure real claps and tune the detector against them
├── setup_gcal.py        # one-time Google Calendar OAuth
├── ghost/
│   ├── live_voice.py    # Gemini Live session: mic, receive loop, idle watchdog
│   ├── brain.py         # system prompt, Gemini client, turn-based fallback path
│   ├── clock.py         # time-of-day awareness for the system prompt
│   ├── alarm_runner.py  # detached one-shot alerter, outlives Ghost closing
│   ├── ui3d.py          # pywebview window
│   ├── webui/           # WebGL particle sphere that reacts to Ghost's voice
│   └── skills/          # 22 auto-loaded modules, 60 registered tools
├── docs/TRIED_AND_SHELVED.md
└── .env                 # keys — never committed
```

Skills are plain Python modules. A `@register({...})` decorator publishes the
tool schema to the model, so adding a capability means dropping in one file
rather than touching the core.

## How a turn works

```
mic ──▶ Gemini Live session (single bidirectional socket)
             │  speech-in, reasoning, tool-calling, speech-out
             ▼
       skills engine ──▶ tool result ──▶ back into the same session
             │
             ▼
       audio out  +  particle sphere reacts to output amplitude
```

## Getting started

**Requirements:** Windows 10/11, Python 3.10+ (developed on 3.14), a Google AI
API key.

```bash
git clone https://github.com/manthunn/ghost-assistant.git
cd ghost-assistant
pip install -r requirements.txt
```

Create `.env`:

```
GOOGLE_API_KEY=...      # required
NOTION_TOKEN=...        # optional, for the Notion skill
MOODLE_ICAL_URL=...     # optional, for the Moodle calendar - get it from
                        # learning.monash.edu -> Calendar -> gear icon ->
                        # Export calendar -> "This calendar"
```

Calendar needs a one-time OAuth step — enable the Google Calendar API, create a
**Desktop app** OAuth client, save it as `credentials.json`, then:

```bash
python setup_gcal.py
```

Then:

```bash
python main.py          # or: py wake.py  to start on "hey ghost"
```

Say **"goodbye ghost"** to exit.

## Lessons from building this

- **Local inference is a VRAM problem before it's a quality problem.** Every
  local component worked in isolation and lost on latency once it had to share a
  6 GB card with a browser and an IDE. Measuring that honestly is what produced
  the current hybrid split.
- **UI trees are virtualised, and absence is not emptiness.** A collapsed Outlook
  account exposes *no* child rows to UIA — so "no inbox found" looked identical to
  "no mail". Ghost silently skipped a whole mailbox for weeks. The fix is
  structural: expand before reading, and never let an unreadable scope render as
  an empty one.
- **The state you test in is rarely the state it runs in.** Mail reading passed
  every test against an already-open Outlook and broke on a cold start, because
  the window appears about 5 seconds before the folder pane is populated. Poll for
  the thing you actually need, not for the window.
- **A model with no clock will guess, and it guesses "morning".** The system
  prompt claimed each message carried a timestamp; the Live path never added one.
  Nothing errored — it just greeted you cheerfully at 11pm.
- **Fixtures you write yourself only test your own assumptions.** Double-clap
  detection passed a full synthetic suite through three rounds of tuning while
  being completely broken in the room. The bug was inverted: the decay test used
  an absolute floor, and real claps hit full scale and ring for ~86 ms, so *the
  louder the clap the more certainly it was discarded*. What found it was
  `clap_calibrate.py` — record real audio, run the production detector over it.
  Build that harness first for anything sensor-facing.
- **`pythonw.exe` has no console, so `sys.stdout` is `None`.** An unguarded
  `sys.stdout.reconfigure()` raises on the first line and pythonw has nowhere to
  print it, so a Startup-launched listener dies in milliseconds looking exactly
  like a feature that "just doesn't work".

## Roadmap

- [ ] Scheduled/background runs (currently on-demand or triggered by a session gap)
- [ ] Email sending — compose-only today; new Outlook has no COM, so this needs Microsoft Graph
- [ ] Cross-AI delegation to non-Gemini models

## License

MIT
