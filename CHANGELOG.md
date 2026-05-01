# Changelog

All notable changes to MyGG are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/pedropedropedropedrop/MyGG/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/pedropedropedropedrop/MyGG/releases/tag/v0.1.0
