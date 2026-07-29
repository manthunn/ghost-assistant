# Ghost — Fully Local AI Assistant

A privacy-first voice assistant that runs entirely on your own machine. No cloud round-trips for the core loop: local LLM inference, local speech recognition, offline text-to-speech, and a modular skills engine that lets Ghost actually *do* things — control the system, automate the browser, manage media, delegate to sub-agents, and remember things between sessions.

Built from scratch in Python on Windows.

## Why local?

Most assistants stream your voice and data to someone else's servers. Ghost doesn't. The language model, the speech pipeline, and the memory all run on local hardware. That constraint shaped the architecture: model serving has to be memory-efficient, the speech loop has to be fast enough to feel conversational, and every capability has to work as a local module.

## Features

- **Local LLM brain** — Llama 3.1 served through [Ollama](https://ollama.com), with context length managed (`num_ctx`) to stay stable across long sessions
- **Real-time speech input** — [faster-whisper](https://github.com/SYSTRAN/faster-whisper) for low-latency transcription ("ears")
- **Voice output** — spoken replies on every turn
- **Modular skills engine** — auto-loaded skill modules, discovered at boot:
  - `system_control` — apps, system actions
  - `browser` — browser automation
  - `media` — playback / media control
  - `agents` — multi-agent delegation for subtasks
  - `memory` — persistent memory across sessions
- **Live status overlay** — a lightweight always-on-top window showing Ghost's state in real time (Listening → Thinking → Working → Speaking)
- **Threaded architecture** — UI runs on the main thread; the assistant loop runs on a daemon thread and pushes status updates into the overlay via callbacks

## Project structure

```
ghost-assistant/
├── main.py              # entry point: wires UI + assistant loop threads
├── ghost/
│   ├── brain.py         # LLM layer (Ollama / Llama 3.1)
│   ├── voice.py         # speech: faster-whisper in, TTS out
│   ├── ui.py            # status overlay (GhostUI)
│   └── skills/
│       ├── agents.py
│       ├── browser.py
│       ├── media.py
│       ├── memory.py
│       └── system_control.py
├── requirements.txt
└── .env                 # local config/keys — never committed
```

## How a turn works

```
mic ──▶ voice.listen() (faster-whisper)
              │
              ▼
        brain.think(heard, status=cb) ──▶ Ollama (Llama 3.1)
              │                                │
              ▼                                ▼
        skills engine ◀───── skill / tool calls
              │
              ▼
        reply ──▶ voice.speak() ──▶ audio out
              │
              ▼
        overlay updates: Listening → Thinking → Working → Speaking
```

Skills are plain Python modules in `ghost/skills/`. The loader (`load_all()`) discovers and registers them at boot — adding a capability means dropping in one module, not touching the core.

## Getting started

### Requirements
- Windows 10/11
- Python 3.10+ (developed on 3.14)
- [Ollama](https://ollama.com) installed and running
- Enough RAM for Llama 3.1 (default 8B tag)

### Setup
```bash
git clone https://github.com/manthunn/ghost-assistant.git
cd ghost-assistant
pip install -r requirements.txt
ollama pull llama3.1
python main.py
```

Say **"goodbye ghost"** to exit.

## Lessons from building this

- **Local model serving is a memory problem first.** Early versions crashed under load until the context window was capped (`num_ctx`) — unbounded context is the silent killer of local LLM apps.
- **Components that work alone break together.** The STT loop, model calls, TTS, and overlay compete for threads and memory; most of the real engineering was making them coexist, which is why the UI and assistant loop live on separate threads.
- **Debugging *is* the work.** The first version of almost every subsystem failed. Tracing and fixing is the core loop, not the cleanup phase.

## Roadmap

- [ ] Wake-word detection
- [ ] Cross-platform TTS
- [ ] Configurable model backends

## License

MIT
