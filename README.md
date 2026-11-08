# MyGG

MyGG is a personal assistant you talk to in a private Telegram chat. She runs on your computer, remembers your to-dos and can use the accounts you choose to connect. Each friend installs their own copy and creates their own Telegram bot.

**[Install MyGG and send your first message →](ONBOARDING.md#install-mygg)** · [Download ZIP](https://github.com/pedropedropedropedrop/MyGG/archive/refs/heads/main.zip)

Already running an earlier version? [Update your installation](ONBOARDING.md#update-an-existing-installation).

## What you can do

| In Telegram | What to set up |
| --- | --- |
| Chat, keep a to-do list and set timed reminders. Try “Remind me in ten minutes to check the oven.” | [Basic installation](ONBOARDING.md#install-mygg). Choose Claude or a local Ollama model. |
| Ask about a photo, screenshot, PDF or text file. Use web search, web fetch or hosted code execution when supported by your Claude model. | [Model and file support](ONBOARDING.md#choose-a-chat-model). Text files work with either backend. |
| Get morning and evening briefings, plus alerts about upcoming meetings. | [Briefings](ONBOARDING.md#briefings) use your timezone and connected sources. |
| Give GG a name to call you, preferred writing style or other standing instructions. | [Personalize GG](ONBOARDING.md#personalize-gg). |
| Recall facts across restarts. Try “Remember that I prefer afternoon appointments.” | [Long-term memory](ONBOARDING.md#long-term-memory) uses Ollama and Qdrant. |
| Dictate a message, request a spoken reply or turn a meeting recording into notes, decisions and to-dos. | [Voice and meetings](ONBOARDING.md#voice-and-meetings). Spoken replies use Edge TTS in Claude mode. |
| Check calendars, schedule events, search inboxes, and send or reply to email across your connected accounts. Review outgoing mail and guest events in Telegram before approving. | [Google Calendar and Gmail](ONBOARDING.md#google-calendar-and-gmail). |
| Get weather for your city or share a Telegram location to change it. | [Weather](ONBOARDING.md#weather) uses OpenWeatherMap. |
| See WHOOP recovery, sleep, strain, workouts and profile data. | [WHOOP](ONBOARDING.md#whoop). |
| Search new WhatsApp messages and ask for a catch-up on a chat. | [Read-only WhatsApp bridge](ONBOARDING.md#whatsapp). |

The computer must be awake and online to answer you or deliver reminders. Claude uses a paid Anthropic API key. Ollama runs model inference on your own hardware. Telegram still carries your messages, and the integrations you connect exchange data with their providers.

## Project reference

- [Installation, optional features and troubleshooting](ONBOARDING.md)
- [Data, credentials and security reporting](SECURITY.md)
- [How the code fits together](ARCHITECTURE.md) · [Contributing a module or fix](CONTRIBUTING.md)
- [Release history](CHANGELOG.md) · [Roadmap](ROADMAP.md) · [MIT license](LICENSE)
