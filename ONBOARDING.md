# Set up your own GG

You need a computer that can stay on, a Telegram account, and either a paid Anthropic API key or a local Ollama model. Start with a private text conversation. You can add each integration independently after that.

**[Install](#install-mygg) · [Choose a model](#choose-a-chat-model) · [Verify your first chat](#first-conversation) · [Optional features](#optional-features) · [Upgrade](#update-an-existing-installation) · [Troubleshooting](#troubleshooting)**

## Install MyGG

### 1. Download the project

Install Python **3.12**. Windows users should include the Python launcher when installing Python. [Python downloads](https://www.python.org/downloads/)

**Download the ZIP (easiest).** Download and fully extract the archive into a folder you can keep. Open the extracted folder containing `main.py` and `requirements.txt`. On Windows, click the File Explorer address bar, type `powershell` and press Enter. On macOS/Linux, open Terminal, type `cd ` (with a space), drag the extracted folder into the window and press Return. Continue to step 2. [Download MyGG ZIP](https://github.com/pedropedropedropedrop/MyGG/archive/refs/heads/main.zip)

**Or clone with Git.** If you prefer updates via `git pull`, install Git and run these commands in PowerShell or Terminal. [Git downloads](https://git-scm.com/downloads)

```sh
git clone https://github.com/pedropedropedropedrop/MyGG.git
cd MyGG
```

### 2. Install the base packages

**Windows PowerShell**

```powershell
py -3.12 -m venv venv
Set-Alias python "$PWD\venv\Scripts\python.exe"
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

**macOS or Linux**

```sh
python3.12 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Leave this terminal open. All remaining `python` commands work as written. Each time you open a new terminal in this folder, repeat the `Set-Alias` line (Windows) or `source venv/bin/activate` (macOS/Linux).

### 3. Connect Telegram

1. In Telegram, open the verified **@BotFather**, send `/newbot`, choose a display name and a unique username ending in `bot`. Copy the token it gives you. [Telegram's bot guide](https://core.telegram.org/bots/tutorial)
2. Find your personal numeric Telegram ID. One way is to message **@userinfobot** from your account and read the ID it returns. Your numeric ID and your bot token are different values. Only enter the token in your own installation.
3. Open **`.env`** in a text editor. From your terminal, use `notepad .env` on Windows, `open -e .env` on macOS, or `nano .env` on Linux. Replace the values in the existing lines as shown below, using your real token and ID. Use your personal ID on both ID lines if you want scheduled briefings. You can leave `OWNER_CHAT_ID=` blank to turn them off.

```dotenv
TELEGRAM_BOT_TOKEN=PASTE_YOUR_BOT_TOKEN_HERE
ALLOWED_USER_IDS=YOUR_NUMERIC_TELEGRAM_ID
OWNER_CHAT_ID=YOUR_NUMERIC_TELEGRAM_ID
USER_NAME=Alex
TIMEZONE=America/New_York
```

Use a timezone such as `America/Chicago` or `Europe/London`. Each person should run their own copy. Anyone you add to your allowlist shares access to the same connected accounts and stored data.

## Choose a chat model

**Claude (default).** Create an API key in your Anthropic Console and enable API billing. A Claude chat subscription alone does not provide API credit. Set `LLM_BACKEND=anthropic` and paste the key into `ANTHROPIC_API_KEY` in `.env`. Chat and scheduled AI work use your API account. [Anthropic Console](https://console.anthropic.com/) · [Claude API guide](https://platform.claude.com/docs/en/api/overview)

**Ollama (local).** Install Ollama on the same computer and start it. Download the default tool-capable model and check it is installed. If the service is stopped, start the Ollama application or run `ollama serve` in a separate terminal. [Ollama setup](https://docs.ollama.com/quickstart) · [Default model and download size](https://ollama.com/library/qwen3:8b)

```sh
ollama pull qwen3:8b
ollama list
```

Set these lines in `.env`. You can leave `ANTHROPIC_API_KEY` empty.

```dotenv
LLM_BACKEND=local
LOCAL_MODEL=qwen3:8b
```

Local chat uses Ollama's service on port 11434. GG also routes meeting analysis, briefings and WhatsApp summaries through the local model. It supports text uploads and transcribed voice. Select Claude when you need images, PDFs, Anthropic web tools, hosted code execution or Edge speech output. Local mode still uses Telegram and any external accounts you connect.

If tool calls drop details or replies are cut off, check the model's context window with `ollama ps` while it is loaded. If it is below 16K, set `OLLAMA_CONTEXT_LENGTH=16384` in the **Ollama server environment** and restart Ollama. A larger window uses more memory. [Ollama context length](https://docs.ollama.com/context-length) · [Set Ollama environment variables on your OS](https://docs.ollama.com/faq#how-do-i-configure-ollama-server)

## First conversation

1. Save `.env`. From the MyGG folder run `python validate.py` using the virtual environment from step 2. Correct any reported setup errors, then run `python main.py`. Keep the terminal open.
2. Open a **private chat** with your own bot and send **`/start`**. A greeting confirms the Telegram connection and your access. Send **`/model`** to see the selected model.
3. Send **“Add a to-do to try GG tomorrow”**, then **“Show my to-dos.”** Seeing the item confirms model access and local storage. Try **“Remind me in two minutes to stretch”** with the bot running to test reminders.

Press **Ctrl+C** in the terminal to stop GG. Run the same command to start again. Restart GG after editing `.env`. In Claude mode, `/opus` changes the chat to Opus, `/sonnet` restores the default, and a message starting with `!!` requests Opus for one turn.

## Optional features

### Personalize GG

Copy `personality.md.example` to `personality.md` beside `main.py`. Edit it with facts and preferences you want GG to use, such as “Use short replies” or “Suggest meetings after 10 a.m.” That text becomes part of the chat model's prompt. Keep passwords and account keys in their designated configuration fields.

### Briefings

Set `OWNER_CHAT_ID` to your own allowlisted Telegram ID and set `TIMEZONE` in `.env`. Start the private chat once and leave GG running. The morning briefing is scheduled for **7:30 a.m.** and the evening wrap-up for **9 p.m.** in your chosen timezone. Connecting Google enables upcoming-meeting alerts. Weather and WHOOP add context when those accounts are configured.

### Long-term memory

To-dos and reminders already persist in a local SQLite file. Add semantic memory if you want GG to recall facts from earlier conversations.

1. Install and start Ollama and Docker. Docker Desktop is one way to run Docker on Windows or macOS. [Ollama](https://docs.ollama.com/quickstart) · [Docker Desktop](https://www.docker.com/products/docker-desktop/)
2. From the project folder install the memory packages and download the extraction and embedding models.

   ```sh
   python -m pip install -r requirements/memory.txt
   ollama pull qwen3:8b
   ollama pull nomic-embed-text
   ```

3. Start a Qdrant container on the loopback interface. The named Docker volume keeps memories across container restarts. A returning user should start their existing container to keep its existing storage. [Qdrant local setup](https://qdrant.tech/documentation/quickstart/)

   ```sh
   docker run -d --name mygg-qdrant --restart unless-stopped -p 127.0.0.1:6333:6333 -v mygg-qdrant-data:/qdrant/storage qdrant/qdrant
   ```

4. Open `http://127.0.0.1:6333/dashboard` locally to check Qdrant. Set `ENABLE_MEMORY=true` in `.env` and restart GG. Keep Ollama and Qdrant running.
5. Send **“Remember that my favorite tea is jasmine.”** Restart GG and ask **“What is my favorite tea?”** The restart clears the short chat history, so this tests long-term recall.

### Voice and meetings

1. Run `python -m pip install -r requirements/voice.txt` and restart GG. For a typical laptop, use `WHISPER_DEVICE=cpu` and `WHISPER_MODEL=small`. On first use, Whisper downloads the model. NVIDIA users who have installed the matching CUDA libraries can select `cuda`. [Whisper installation and GPU requirements](https://github.com/SYSTRAN/faster-whisper)
2. Send a short voice note and check GG's reply. In Claude mode, ask **“Read that back to me as a voice note.”** Edge TTS sends the reply text to Microsoft's speech service. Set `TTS_VOICE` to choose a voice.
3. To create meeting notes, attach a recording as an audio document. Voice notes over 30 seconds also enter meeting processing. Add a caption for context. GG saves the transcript in `meeting_transcripts/`, extracts decisions and can add your action items to the to-do list. Try **“Show my latest meeting notes.”**

### Google Calendar and Gmail

1. Create a project in Google Cloud. Enable **Google Calendar API** and **Gmail API**. Configure the OAuth consent screen. If the app is in testing, add your Google account as a test user. [Calendar setup](https://developers.google.com/workspace/calendar/api/quickstart/python) · [Gmail setup](https://developers.google.com/workspace/gmail/api/quickstart/python)
2. Create an **OAuth client ID** of type **Desktop app**. Download its JSON and save it as `credentials.json` beside `main.py`.
3. Run `python setup_calendar.py` on the computer running GG. In the browser, select the account you want to connect, review the requested Calendar and Gmail permissions, and approve. The helper uses a temporary callback on port 8090 and saves `token.json`.
4. Restart GG. Ask **“What's on my calendar tomorrow?”** or **“List my last three unread emails.”** To test outgoing email, ask GG to send a message to yourself. In the private Telegram preview, review the recipient and content, then choose **Send** or **Cancel**. Replies and calendar events with guests also require approval. Only the newest preview in that chat is valid, for ten minutes. Restarting clears pending approvals.

To connect a second account, add `GOOGLE_EXTRA_ACCOUNTS=work` to `.env`. Optionally set `GOOGLE_ACCOUNT_WORK_LABEL=Work`. Run `python setup_calendar.py work`, sign in to that account and restart GG. Ask **“Show tomorrow's events on my work calendar.”** For several accounts, list their names separated by commas. Start each name with a lowercase letter and use lowercase letters, digits or underscores.

### Weather

Create an OpenWeatherMap API key and set `OPENWEATHER_API_KEY` in `.env`. Set `DEFAULT_LOCATION` to a city and country, such as `London,GB`. Restart and ask **“What's the weather?”** Sharing a location in Telegram updates the location GG uses. [OpenWeatherMap](https://openweathermap.org/api)

### WHOOP

Register a WHOOP developer app with the redirect URI `http://localhost:8788/callback`. In `.env`, set `WHOOP_CLIENT_ID`, `WHOOP_CLIENT_SECRET` and `WHOOP_REDIRECT_URI` to the matching values. Run `python whoop_auth.py`, complete consent in the browser and restart GG. Ask **“What was my last WHOOP recovery score?”** The OAuth helper stores the grant in `.whoop_token.json`. [WHOOP developer guide](https://developer.whoop.com/docs/developing/oauth/)

### WhatsApp

The optional read-only bridge links as a WhatsApp device and stores new messages and captions it receives while running. It skips offline history. You can search that local message history immediately. With long-term memory enabled, a scheduled summary also processes messages through your selected model and saves relevant context.

1. Install Node.js 22.12 or newer with npm. From the MyGG folder run `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Paste the generated value into `WHATSAPP_BRIDGE_SECRET` in the project's `.env`. Both processes read this same file. [Node.js downloads](https://nodejs.org/en/download)
2. Restart GG. In a **second terminal** inside the MyGG folder, run the following commands and leave it open.

   ```sh
   cd whatsapp-bridge
   npm ci
   npm start
   ```

3. When pairing is requested, open the QR image `whatsapp-bridge/qr.png` on the computer. On your phone, go to WhatsApp's **Linked devices → Link a device** and scan it. The bridge saves its session in `whatsapp-bridge/auth_state/`.
4. After a new test message arrives, ask GG **“Summarize my WhatsApp from the last hour.”** Keep the Python bot and bridge running. If the Python receiver is unavailable, the bridge keeps messages in `whatsapp-bridge/pending-delivery/` and retries. To disconnect, stop the bridge and unlink it under **Linked devices** on your phone.

### Keep GG running

For regular use, leave the foreground terminal open or use the Windows launcher `.\start-gg.ps1` from the project folder. A sleeping computer pauses the bot. For automatic startup on Windows, first verify the foreground setup, install NSSM on your PATH, stop the terminal copy, then run `.\setup_service.bat` from an administrator terminal. It registers a service named `MyGG`. Use `nssm start MyGG`, `nssm status MyGG` and `nssm stop MyGG` to manage it. Service logs are in `logs/`. Start Ollama, Docker and the WhatsApp bridge separately when you use them. [NSSM](https://nssm.cc/download)

## Update an existing installation

1. Stop the bot and bridge. Back up your `.env`, `personality.md`, `pa.db` and any `pa.db-*` files. If connected, also back up transcripts, OAuth files and WhatsApp's `auth_state/` and `pending-delivery/` privately. Keep your existing Qdrant volume or storage directory and its backup.
2. **Git installations:** From the MyGG folder run `git status --short`. Save any custom code edits before running `git pull --ff-only`. **ZIP installations:** Rename your stopped installation folder as a backup. Extract the new ZIP to the original path, with `main.py` directly inside. Repeat the virtual environment setup in step 2 of [Install MyGG](#install-mygg), then restore the private files from step 1. Keep any existing Qdrant storage at the path used by its container. Reapply any intentional code customizations.
3. Using this installation's virtual environment, run `python -m pip install -r requirements.txt`. Update each add-on you use with `python -m pip install -r requirements/memory.txt` or `python -m pip install -r requirements/voice.txt`. For WhatsApp run `npm ci` in `whatsapp-bridge/`.
4. Compare the new `.env.example` with your existing `.env`. **Keep your current file and credentials.** Check the allowed ID, set `OWNER_CHAT_ID` to an allowed ID or blank, and set `ENABLE_MEMORY=true` if using your existing memory store. Preserve your memory collection, model settings and username. Run `python validate.py`, restart your services and repeat the [first conversation](#first-conversation).

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Python can't import a package | Run the install command with the same virtual-environment Python that launches `main.py`. On Windows, using `.\venv\Scripts\python.exe` directly bypasses activation problems. |
| GG is silent | Confirm the terminal is still running. Open a private chat with the bot created by your token, send `/start`, and check the numeric ID in `ALLOWED_USER_IDS`. Only one running process can poll a token. |
| The greeting works, then the first AI reply fails | Use `/model`. For Claude, check the API key, billing and model access. For local mode, check `ollama list`, the running service and the exact `LOCAL_MODEL` name. |
| A meeting, image or voice feature fails | Check the model's [supported inputs](#choose-a-chat-model), install voice packages for transcription and allow time for the first download. Use `cpu` if CUDA libraries are missing. |
| Facts disappear after a restart | Confirm memory is enabled, `docker start mygg-qdrant` can start the existing container, and `ollama list` includes both memory models. Check that the Qdrant volume, collection and username match the original setup. |
| Google or WHOOP authorization fails | Check the selected Google project, enabled APIs, desktop client and test user. For WHOOP, compare the callback URI character-for-character. Re-run the appropriate authorization helper. |
| WhatsApp stays empty | Check the linked device, both running terminals and the shared secret. Test with a newly received text after pairing. |

For a reproducible problem, include your OS, Python version, backend and a redacted error when opening an issue. Keep tokens, conversations, contact names, OAuth files and QR images private. [Issue tracker](https://github.com/pedropedropedropedrop/MyGG/issues) · [Security reporting](SECURITY.md)
