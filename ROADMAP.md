# Roadmap

MyGG is a personal-scale project. The roadmap reflects what one person can realistically maintain — pull requests are welcome to accelerate any of these.

## Done — `v0.1.0`

- [x] Telegram bot with text, voice, and image input
- [x] Voice input via faster-whisper, voice output via Edge TTS
- [x] Long-term memory (mem0 + Qdrant + Ollama)
- [x] Google Calendar + Gmail (read, send, reply)
- [x] Todos, reminders, weather
- [x] WHOOP v2 integration (recovery, sleep, strain, workouts, profile)
- [x] WhatsApp read-only bridge with summarization
- [x] Meeting transcription and structured notes
- [x] Auto-discovered modules system
- [x] Heartbeat: morning briefing, evening wrap-up, pre-meeting alerts
- [x] Configurable `personality.md`
- [x] Windows service install

## Available since the initial release

- [x] Local chat model via Ollama, including local meeting and WhatsApp summaries.
- [x] Text-only setup with separate voice and memory packages.
- [x] Telegram approval for outgoing email and calendar invitations.

## Near-term ideas

- [ ] One-shot install scripts for macOS (`install.sh`) and Linux (`install.sh`)
- [ ] Pluggable LLM provider — swap Anthropic for an OpenAI-compatible endpoint
- [ ] Pluggable vector store — swap Qdrant for Chroma or pgvector
- [ ] More example modules (Google Tasks, Apple Reminders bridge, Spotify, Notion)
- [ ] First-run wizard that walks the user through `.env` and OAuth interactively
- [ ] Optional web dashboard for memory inspection and conversation history
- [ ] Test coverage above 50%

## Mid-term — `v0.3.x`

- [ ] Mobile companion (iOS / Android) using Telegram as transport, but with native widgets
- [ ] Multi-user mode (per-user mem0 namespacing, per-user OAuth tokens, per-user personality)
- [ ] Plugin marketplace pattern — `pip install mygg-spotify`-style external modules

## Long-term — `v1.0.0`

- [ ] MCP (Model Context Protocol) server — MyGG modules become MCP tools usable from Claude Desktop / Code
- [ ] Encrypted memory at rest
- [ ] Differential privacy for shared deployments
- [ ] First-class support for self-hosted on a Raspberry Pi / mini PC

## Not planned

- Proprietary cloud-hosted SaaS version. MyGG is and will remain self-hosted.
- Built-in advertising or monetization hooks.
- Locking features behind a paid tier.

## How to influence the roadmap

- Open an issue with the `enhancement` label to propose a feature
- Open an issue with the `discussion` label for architectural questions
- Send a pull request — explicit code is more persuasive than any roadmap entry
