# Ghost — Fully Local AI Assistant

A privacy-first voice assistant that runs entirely on your own machine. No cloud APIs, no data leaving your computer: local LLM inference, local speech recognition, local text-to-speech, and a modular skills engine that lets Ghost actually *do* things — control the system, automate the browser, search the web, manage media, and remember things between sessions.

Built from scratch in Python on Windows.

## Why local?

Most assistants stream your voice and data to someone else's servers. Ghost doesn't. Everything — the language model, the speech pipeline, the memory — runs on local hardware. That constraint shaped the whole architecture: model serving has to be memory-efficient, the speech loop has to be fast enough to feel conversational, and every capability has to work without an internet-dependent API.

## Features

- **Local LLM** — [Llama 3.1](https://ollama.com) served through Ollama, with context length tuned (`num_ctx=8192`) to stay within memory limits during long sessions
- **Real-time speech recognition** — [faster-whisper](https://github.com/SYSTRAN/faster-whisper) for low-latency transcription
- **Native text-to-speech** — Windows TTS for spoken responses
- **Modular skills engine** — decorator-based skill registration with auto-loading modules:
  - System control (apps, volume, power, etc.)
  - Browser automation
  - Web search
  - Spotify / media control
  - Multi-agent delegation (Ghost can spin up sub-agents for subtasks)
  - Persistent memory across sessions
- **Status overlay** — lightweight tkinter UI showing listening/thinking/speaking state

## Architecture

```
Voice input ──▶ faster-whisper (STT)
                     │
                     ▼
              Intent + prompt ──▶ Ollama (Llama 3.1, local)
                     │                     │
                     ▼                     ▼
              Skills engine ◀──── tool / skill calls
              (auto-loaded modules)
                     │
                     ▼
              Action + response ──▶ Windows TTS ──▶ Voice output
                     │
                     ▼
              Persistent memory store
```

Skills are plain Python modules dropped into the skills directory. A decorator registers each function with its trigger metadata, and the loader picks them up automatically at startup — adding a capability means writing one function, not touching the core.

## Getting started

### Requirements
- Windows 10/11
- Python 
- [Ollama](https://ollama.com) installed and running
- A machine with enough RAM/VRAM for Llama 3.1 8B 

### Setup
```bash
git clone https://github.com/manthunn/ghost-assistant.git
cd ghost-assistant
pip install -r requirements.txt   
ollama pull llama3.1
python main.py                  
```


## Lessons from building this

- **Local model serving is a memory problem first.** Early versions crashed under load until I capped Ollama's context window (`options={"num_ctx": 8192}`) — unbounded context is the silent killer of local LLM apps.
- **Components that work alone break together.** The STT loop, model calls, TTS, and overlay all compete for the main thread and for memory; most of the real engineering was in making them coexist.
- **Debugging *is* the work.** The first version of almost every subsystem failed. The project taught me to treat tracing and fixing as the core loop, not the cleanup phase.

## Roadmap

- [ ] Wake-word detection
- [ ] Cross-platform TTS
- [ ] Configurable model backends
