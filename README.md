# MyGG

MyGG is a personal assistant you talk to in a private Telegram chat. She runs on your computer, remembers your to-dos and can use the accounts you choose to connect. Each friend installs their own copy and creates their own Telegram bot.

**[Install MyGG and send your first message →](ONBOARDING.md#install-mygg)** · [Download ZIP](https://github.com/pedropedropedropedrop/MyGG/archive/refs/heads/main.zip)

Already running an earlier version? [Update your installation](ONBOARDING.md#update-an-existing-installation).

## What you can do

| In Telegram | What to set up |
| --- | --- |
| Chat, keep a to-do list and set timed reminders. Try “Remind me in ten minutes to check the oven.” | [Basic installation](ONBOARDING.md#install-mygg). Choose Claude or a local Ollama model. |
| Read text files and PDFs, ask about photos or screenshots, and research the web. Claude can also run hosted code. | [Model, file and web support](ONBOARDING.md#choose-a-chat-model). Local photos need a vision-capable model. |
| Get morning and evening briefings, plus alerts about upcoming meetings. | [Briefings](ONBOARDING.md#briefings) use your timezone and connected sources. |
| Give GG a name to call you, preferred writing style or other standing instructions. | [Personalize GG](ONBOARDING.md#personalize-gg). |
| Recall facts across restarts. Try “Remember that I prefer afternoon appointments.” | [Long-term memory](ONBOARDING.md#long-term-memory) uses Ollama and Qdrant. |
| Dictate a message, request a spoken reply or turn a meeting recording into notes, decisions and to-dos. | [Voice and meetings](ONBOARDING.md#voice-and-meetings). Choose Edge speech or a local multilingual voice with an optional reference recording. |
| Check calendars, schedule events, search inboxes, and send or reply to email across your connected accounts. Review outgoing mail and guest events in Telegram before approving. | [Google Calendar and Gmail](ONBOARDING.md#google-calendar-and-gmail). |
| Get weather for your city or share a Telegram location to change it. | [Weather](ONBOARDING.md#weather) uses OpenWeatherMap. |
| See WHOOP recovery, sleep, strain, workouts and profile data. | [WHOOP](ONBOARDING.md#whoop). |
| Search or summarize WhatsApp messages, and approve a text to an individual or group. | [WhatsApp linked-device setup](ONBOARDING.md#whatsapp). |
| Update tasks, save goals, review progress and tell GG to snooze or dismiss a recurring suggestion. | [Tasks, goals and follow-ups](ONBOARDING.md#tasks-goals-and-follow-ups). SQLite is built in. Google Tasks is optional. |
| Ask GG to review your project board or look up information in your own notes. | [Project board and knowledge sources](ONBOARDING.md#project-board-and-knowledge-sources). |
| Get proactive research, inbox triage, a Friday leisure menu and connection-health notices. | [Briefings and initiative](ONBOARDING.md#briefings). |
| Ask GG to develop a new module in your own copy, run her unattended or check for upstream updates. | [Owner-requested code changes](ONBOARDING.md#owner-requested-code-changes) · [Supervision and recovery](OPERATIONS.md). |

The computer must be awake and online to answer you or deliver reminders. Claude uses a paid Anthropic API key. Ollama runs model inference on your own hardware. Telegram still carries your messages, and the integrations you connect exchange data with their providers.

## Project reference

- [Installation, optional features and troubleshooting](ONBOARDING.md)
- [Data, credentials and security reporting](SECURITY.md)
- [How the code fits together](ARCHITECTURE.md) · [Contributing a module or fix](CONTRIBUTING.md)
- [Release history](CHANGELOG.md) · [Roadmap](ROADMAP.md) · [MIT license](LICENSE)
