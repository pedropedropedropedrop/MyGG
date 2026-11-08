# Changelog

All notable changes to MyGG are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] — 2026-11-07

- A smaller first install for Telegram chat, to-dos and reminders. Memory and voice have their own optional requirements. The setup guide covers Windows, macOS and Linux, an offline configuration check, and how to upgrade an existing installation.
- Run chat, meeting analysis and WhatsApp summaries through a local Ollama model, or use Claude with the existing cloud features.
- Private Telegram chats require an explicit allowlist. Email sends, replies and calendar events with guests show an approval preview.
- Failed reminder deliveries stay pending for retry. The WhatsApp bridge queues incoming messages across bot outages and can send a text after its exact recipient and contents are approved in Telegram.
- Set up additional Google accounts in `.env`. The example settings and the guide explain how to enable and verify each integration.
- Start with empty goals, editable tasks, feedback and conversation history. Configure Google Tasks, a project board or a private knowledge collection, and enable a personal schedule for briefings, inbox triage, follow-ups and leisure suggestions.
- Added local PDF reading, optional image understanding, local-model web research and multilingual Chatterbox speech with a user-supplied voice reference. Model selection can be configured per workload.
- Advanced installations can enable connection checks, process supervision, verified updates, local inference recovery and owner-requested code changes in their own Git checkout.

## [0.1.0] — 2026-05-01

Initial public release.

### Added
- Telegram bot interface with text, voice, and image input
- Claude tool-use agent loop with adaptive thinking and prompt caching
- Long-term memory via mem0 (Qdrant + Ollama embeddings)
- Google Calendar and Gmail integration (single-account by default, multi-account configurable)
- Todos and reminders backed by SQLite
- Proactive heartbeat: morning briefing, evening wrap-up, pre-meeting alerts
- Voice in (faster-whisper STT) and voice out (Edge TTS)
- Meeting transcription and structured note extraction
- Auto-discovered modules system — drop a file in `modules/`, it loads automatically
- Built-in modules: `weather` (OpenWeatherMap), `whoop` (recovery / sleep / strain), `meetings`
- WHOOP API v2 integration with OAuth2 authorization-code flow
- Optional WhatsApp read-only bridge (Node.js Baileys sidecar) with mem0 summarization
- Configurable personality via `personality.md`
- Windows-as-a-service install via `setup_service.bat` and `nssm`

### Security
- `ALLOWED_USER_IDS` gates the bot to authorized Telegram user IDs only
- All secrets read from `.env` — never committed
- OAuth tokens cached locally, gitignored
- Bounded retry loops on token refresh to prevent infinite loops

[Unreleased]: https://github.com/pedropedropedropedrop/MyGG/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/pedropedropedropedrop/MyGG/releases/tag/v0.2.0
[0.1.0]: https://github.com/pedropedropedropedrop/MyGG/commit/2d12936c1342f52bb28023091a6b933645f88f04
