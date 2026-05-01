# MyGG

**Your personal AI assistant on Telegram.**

MyGG is a self-hosted Telegram bot powered by Anthropic's Claude. It chats with
you, remembers what you tell it, manages your calendar and email, summarizes
meetings, and quietly follows up on what you have going on. It runs entirely
on your own hardware — your Anthropic key, your Google account, your data.

You bring the API keys; MyGG brings the integration glue. The whole bot is a
few thousand lines of Python plus a small Node.js sidecar for WhatsApp.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![Made with Claude](https://img.shields.io/badge/made%20with-Claude-A569FF.svg)](https://www.anthropic.com/)

---

## Features

- **Natural Telegram chat** — talk to your assistant anywhere, on any device
  Telegram runs.
- **Voice in, voice out** — send a voice note, get a voice reply. Powered by
  faster-whisper (STT, GPU-accelerated) and Edge TTS (free, no key required).
- **Long-term memory** — every conversation feeds a local mem0 + Qdrant vector
  store, so the assistant remembers projects, people, decisions, and
  preferences across sessions.
- **Google Calendar + Gmail** — read, list, create events, send emails,
  reply to emails, with multi-account support.
- **Meeting transcription** — drop in an audio file and MyGG transcribes it,
  pulls out decisions and action items, and turns them into todos.
- **WHOOP integration** — recovery, sleep, strain, workouts (optional).
- **WhatsApp read-only** — searches and summarises your WhatsApp via a
  Baileys-based Node.js sidecar (optional).
- **Weather** — current conditions and morning briefings via OpenWeatherMap.
- **Todos and reminders** — simple, local SQLite store.
- **Modular architecture** — drop a `.py` file into `modules/` to add new
  tools. See `modules/_template.py`.
- **Proactive heartbeats** — daily morning briefing, evening wrap-up, and
  pre-meeting heads-ups, all assembled by Claude from your data.

## Quickstart

```bash
git clone https://github.com/YOUR-USERNAME/MyGG.git
cd MyGG
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
cp .env.example .env           # then edit .env
cp personality.md.example personality.md   # then edit personality.md
python setup_calendar.py
python main.py
```

The full installation walkthrough — Telegram, Anthropic, Qdrant, Ollama,
Google OAuth, optional WHOOP and WhatsApp — lives in
[ONBOARDING.md](ONBOARDING.md). It is written for someone competent but
non-expert; expect to be done in 30–60 minutes.

## Architecture

```
                       ┌────────────────────────┐
                       │       Telegram         │
                       └────────────┬───────────┘
                                    │
                            ┌───────▼────────┐
                            │    main.py     │  routing + lifecycle
                            └───────┬────────┘
                                    │
                            ┌───────▼────────┐
                            │   agent.py     │  Claude tool-use loop
                            └───┬────────┬───┘
                                │        │
              ┌─────────────────┘        └──────────────────┐
              │                                             │
      ┌───────▼──────────┐                          ┌───────▼──────┐
      │   Anthropic API  │                          │  personality │
      │     (Claude)     │                          │     .md      │
      └──────────────────┘                          └──────────────┘
                                    │
                ┌───────────────────┼───────────────────┐
                │                   │                   │
        ┌───────▼──────┐    ┌───────▼──────┐    ┌───────▼──────┐
        │   tools.py   │    │   modules/   │    │ heartbeat.py │
        │ todos / mail │    │ whoop, etc.  │    │  briefings   │
        │ calendar etc │    │              │    │              │
        └───────┬──────┘    └──────────────┘    └──────────────┘
                │
        ┌───────▼─────────────┐
        │  SQLite, mem0,      │
        │  Qdrant, Google,    │
        │  WhatsApp bridge    │
        └─────────────────────┘
```

## Tech stack

| Layer            | Tool                                                              |
| ---------------- | ----------------------------------------------------------------- |
| Language         | Python 3.12                                                       |
| Telegram         | [`python-telegram-bot`](https://github.com/python-telegram-bot/python-telegram-bot) (with job-queue) |
| LLM              | [Anthropic SDK](https://github.com/anthropics/anthropic-sdk-python) (Claude Sonnet / Opus) |
| Long-term memory | [`mem0`](https://github.com/mem0ai/mem0) on top of Qdrant + Ollama |
| Vector DB        | [Qdrant](https://qdrant.tech) (local, via Docker)                 |
| Local LLM        | [Ollama](https://ollama.com) (`qwen3:8b` for memory, `nomic-embed-text` for embeddings) |
| Google           | `google-api-python-client` (Calendar + Gmail)                     |
| Speech-to-text   | [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper)     |
| Text-to-speech   | [`edge-tts`](https://github.com/rany2/edge-tts)                   |
| WhatsApp         | Node.js + [Baileys](https://github.com/WhiskeySockets/Baileys) sidecar |
| WHOOP            | Whoop developer API + `httpx`                                     |
| Storage          | SQLite (todos, reminders, meetings, WhatsApp messages)            |

## Documentation

| Document                                    | What it covers                                            |
| ------------------------------------------- | --------------------------------------------------------- |
| [ONBOARDING.md](ONBOARDING.md)              | Step-by-step installation for non-experts.                |
| [ARCHITECTURE.md](ARCHITECTURE.md)          | Component breakdown, data flow, module pattern.           |
| [CONTRIBUTING.md](CONTRIBUTING.md)          | How to file issues, propose features, send pull requests. |
| [SECURITY.md](SECURITY.md)                  | Threat model, vulnerability reporting.                    |
| [ROADMAP.md](ROADMAP.md)                    | What is done, what is next.                               |
| [CHANGELOG.md](CHANGELOG.md)                | Release history.                                          |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)    | Community expectations.                                   |

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

MyGG stands on the shoulders of:

- [Anthropic Claude](https://www.anthropic.com/) — the brain.
- [mem0](https://github.com/mem0ai/mem0) — long-term memory.
- [Qdrant](https://qdrant.tech) — vector storage.
- [Ollama](https://ollama.com) — local model runtime.
- [Baileys](https://github.com/WhiskeySockets/Baileys) — the WhatsApp bridge.
- [WHOOP](https://www.whoop.com) — fitness data.
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper),
  [edge-tts](https://github.com/rany2/edge-tts) — voice.

If MyGG is useful to you, contribute a module, file a bug, or just say hi
in an issue.
