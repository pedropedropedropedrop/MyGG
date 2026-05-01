# MyGG Onboarding

This is the full installation walkthrough. It assumes you can copy-paste
commands into a terminal but does not assume you are a software engineer.
You should be able to get from "fresh PC" to "talking to your assistant on
Telegram" in 30–60 minutes.

If anything goes wrong, scroll down to **[Troubleshooting](#troubleshooting)**
or paste the entire repository into Claude Code and ask it to debug — that
is genuinely the easiest path when something breaks.

---

## Table of Contents

1. [What you'll end up with](#1-what-youll-end-up-with)
2. [Prerequisites](#2-prerequisites)
3. [Step 1 — Clone the repo and install dependencies](#step-1--clone-the-repo-and-install-dependencies)
4. [Step 2 — Telegram bot](#step-2--telegram-bot)
5. [Step 3 — Anthropic API key](#step-3--anthropic-api-key)
6. [Step 4 — Vector database (Qdrant)](#step-4--vector-database-qdrant)
7. [Step 5 — Local LLM (Ollama)](#step-5--local-llm-ollama)
8. [Step 6 — Google (Gmail + Calendar)](#step-6--google-gmail--calendar)
9. [Step 7 — Personality file](#step-7--personality-file)
10. [Step 8 — Configure .env](#step-8--configure-env)
11. [Step 9 — First run](#step-9--first-run)
12. [Step 10 — Run as a service (optional)](#step-10--run-as-a-service-optional)
13. [Step 11 — Optional features](#step-11--optional-features)
14. [Troubleshooting](#troubleshooting)
15. [Getting help](#getting-help)

---

## 1. What you'll end up with

When you finish this guide, you will have:

- A Telegram bot (your own, with whatever name you choose) that responds
  only to you.
- A Python process running on your computer that talks to that bot,
  Claude, your Google account, and a local memory store.
- Qdrant running in a Docker container, holding everything the assistant
  has remembered about you.
- Ollama running in the background, providing the small local model that
  decides what is worth remembering.
- (Optional) WHOOP integration, weather, WhatsApp read-only access, voice
  responses.

You can chat with your bot from anywhere with Telegram installed. The
heavy lifting happens on your machine — if your machine is off, the bot is
off. (Step 10 covers running it as an always-on background service.)

---

## 2. Prerequisites

You will need accounts and software. Get these set up before you start:

### Software

| What             | Why                                       | Where to get it                                              |
| ---------------- | ----------------------------------------- | ------------------------------------------------------------ |
| Python 3.12+     | The bot runtime.                          | https://www.python.org/downloads/                            |
| Git              | To clone this repo.                       | https://git-scm.com/downloads                                |
| Docker Desktop   | Runs Qdrant locally.                      | https://www.docker.com/products/docker-desktop/              |
| Ollama           | Runs a small local LLM for memory.        | https://ollama.com                                           |
| Node.js 20+      | Only needed for WhatsApp bridge.          | https://nodejs.org                                           |

The instructions assume **Windows 10/11**. macOS and Linux work fine — the
only real differences are file paths, the service-installer step, and how
you start Docker. Where it matters, the differences are called out.

### Accounts

| What                  | Required?         | Notes                                              |
| --------------------- | ----------------- | -------------------------------------------------- |
| Telegram              | Yes               | Free. Install on your phone or use Telegram Web.   |
| Anthropic API         | Yes — and **paid**. | https://console.anthropic.com — small balance is enough to start (5–10 USD). |
| Google account        | Recommended       | For Gmail + Calendar tools.                        |
| OpenWeatherMap        | Optional          | Free tier. https://openweathermap.org/api          |
| WHOOP developer       | Optional          | Free. https://developer.whoop.com (only if you wear a WHOOP). |

---

## Step 1 — Clone the repo and install dependencies

Open a terminal in the folder where you want to keep the project. On
Windows that is PowerShell or Windows Terminal.

```powershell
git clone https://github.com/YOUR-USERNAME/MyGG.git
cd MyGG
```

Create a Python virtual environment so dependencies don't pollute your
system Python:

```powershell
# Windows
python -m venv venv
venv\Scripts\activate
```

```bash
# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

You should see `(venv)` at the start of your prompt. Now install the
Python dependencies:

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

This pulls down `python-telegram-bot`, `anthropic`, `mem0ai`, the Google
client libraries, `faster-whisper`, `edge-tts`, and a few others. It will
take a couple of minutes.

> **Note on faster-whisper and CUDA:** if you have an NVIDIA GPU,
> faster-whisper will use it automatically and voice notes will transcribe
> in seconds. If you do not have a GPU, set `WHISPER_DEVICE=cpu` and
> consider `WHISPER_MODEL=small` in `.env` later — large-v3 on CPU is slow.

---

## Step 2 — Telegram bot

You need to register a bot with Telegram and get its token.

1. Open Telegram. Search for **@BotFather** and start a chat.
2. Send `/newbot`.
3. BotFather will ask for a display name (anything works — e.g.
   "Alex's Assistant").
4. Then it will ask for a username — must end in `bot`, e.g.
   `alex_assistant_bot`.
5. BotFather will reply with a token that looks like
   `1234567890:ABCD-1234abcd_efghEFGH-IJklmnopqrstuvw`.
   **This is your `TELEGRAM_BOT_TOKEN`.** Copy it somewhere safe.

Now find your own Telegram user ID:

1. In Telegram, search for **@userinfobot** and start a chat.
2. Send any message; the bot replies with your user ID (a 9- or
   10-digit number). **This is your `OWNER_CHAT_ID` and your single entry
   for `ALLOWED_USER_IDS`.**

> **Why does this matter?** `ALLOWED_USER_IDS` is the bot's gatekeeper. If
> someone else finds your bot's username and messages it, the bot will
> ignore them unless their ID is in this list. **Do not leave this blank.**

---

## Step 3 — Anthropic API key

1. Go to https://console.anthropic.com and sign up / sign in.
2. Add a payment method and a small amount of credit (5–10 USD goes a long way).
3. Click **API keys** → **Create Key**.
4. Copy the key (starts with `sk-ant-...`). **This is your `ANTHROPIC_API_KEY`.**

> **Cost expectation:** typical usage with Sonnet is fractions of a cent
> per message. Heavy days with big briefings might be tens of cents.
> Anthropic's dashboard shows your spend.

---

## Step 4 — Vector database (Qdrant)

Qdrant is the database where mem0 stores your assistant's long-term memory.

1. Make sure Docker Desktop is running. (Open it; wait for the green
   "Engine running" indicator.)
2. In a terminal, start Qdrant:

   **Windows PowerShell:**
   ```powershell
   docker run -d --name mygg-qdrant `
     -p 6333:6333 -p 6334:6334 `
     -v ${PWD}\qdrant_storage:/qdrant/storage:z `
     qdrant/qdrant
   ```

   **macOS / Linux:**
   ```bash
   docker run -d --name mygg-qdrant \
     -p 6333:6333 -p 6334:6334 \
     -v "$(pwd)"/qdrant_storage:/qdrant/storage:z \
     qdrant/qdrant
   ```

3. Verify it's reachable: open http://localhost:6333/dashboard in your
   browser. You should see Qdrant's UI.

The `qdrant_storage/` directory holds the actual vectors — back it up if
you care about preserving long memories. It is gitignored.

> **No Docker?** Qdrant Cloud has a free tier
> (https://cloud.qdrant.io). Set `QDRANT_HOST` and `QDRANT_PORT` in your
> `.env` to point at the cloud cluster.

---

## Step 5 — Local LLM (Ollama)

Ollama runs the small model mem0 uses to decide what should be remembered
and the embedding model that turns text into vectors.

1. Install Ollama from https://ollama.com (binary installer for Windows,
   macOS, Linux).
2. After installation, pull the two models MyGG needs:

   ```bash
   ollama pull qwen3:8b
   ollama pull nomic-embed-text
   ```

   `qwen3:8b` is around 5 GB; `nomic-embed-text` is around 270 MB. The
   pull-down takes a few minutes on a normal connection.

3. Confirm Ollama is running:

   ```bash
   curl http://localhost:11434/api/tags
   ```

   You should see JSON listing the two models.

> **Low-VRAM machines:** if `qwen3:8b` is too big for your GPU, swap it
> for `llama3.1:8b-instruct-q4_K_M` or `mistral:7b` and update
> `OLLAMA_MEMORY_MODEL` in `.env`.

---

## Step 6 — Google (Gmail + Calendar)

This grants MyGG read/write access to your Gmail and Calendar. The token
lives only on your machine.

1. Open **Google Cloud Console** → https://console.cloud.google.com.
2. Create a new project (top-left dropdown → "New Project"). Name it
   anything — e.g. "MyGG".
3. **Enable the APIs**: search for and enable both
   **Google Calendar API** and **Gmail API** in
   *APIs & Services → Library*.
4. **Configure the OAuth consent screen**:
   - User Type: **External**
   - App name: anything
   - Support email: your email
   - Add yourself as a **Test user** (your Gmail address)
   - Save and continue through all screens
5. **Create OAuth credentials**:
   - *APIs & Services → Credentials → Create Credentials → OAuth client ID*
   - Application type: **Desktop app**
   - Name: anything
   - Click **Create**, then **Download JSON**
   - Save the downloaded file as **`credentials.json`** in the MyGG
     project root (next to `main.py`).

Now run the OAuth flow:

```bash
python setup_calendar.py
```

A browser window will open. Sign in with your Google account, click
"Continue" on the unverified-app warning (it's your own app — click
**Advanced → Go to MyGG (unsafe)**), and grant the requested permissions.

When it succeeds you'll see "Authorization successful! Token saved to
token.json". You can close the browser tab.

> **Adding a second account later** (e.g. work + personal): edit
> `setup_calendar.py` and `tools.py` to add a second entry, then run
> `python setup_calendar.py work` (or whatever name you chose).

---

## Step 7 — Personality file

`personality.md` is the heart of how your assistant knows you. Anything
in this file is loaded directly into the system prompt on every message.

Copy the example and edit it:

```bash
cp personality.md.example personality.md
```

Open `personality.md` in any text editor and customize. Sections to fill in:

- **About me** — name, role, location.
- **What I'm focused on** — your projects, hobbies, recurring commitments.
- **People in my life** — partner, kids, top clients, friends. The
  assistant will remember these and use them.
- **Email accounts** — which address you use for what.
- **Communication preferences** — tone, do's and don'ts, when to be quiet.

Keep it focused and concise. Bullet points are great. Anything you
include affects every response, so every line should pay rent.

---

## Step 8 — Configure .env

Copy the template and fill in the values you collected above:

```bash
cp .env.example .env
```

Open `.env` in your text editor. At a minimum, set:

```env
TELEGRAM_BOT_TOKEN=12345...      # from BotFather (Step 2)
ANTHROPIC_API_KEY=sk-ant-...     # from Anthropic console (Step 3)
ALLOWED_USER_IDS=123456789       # your Telegram user ID (Step 2)
OWNER_CHAT_ID=123456789          # same number, single value
USER_NAME=Alex                   # how the assistant addresses you
TIMEZONE=America/New_York        # your timezone
```

Optional sections (audio, WhatsApp, weather, WHOOP) can stay blank for
now — fill them in later when you set up those features.

---

## Step 9 — First run

In your activated venv, with Docker (Qdrant) and Ollama both running:

```bash
python main.py
```

You should see log lines like:

```
[main] INFO: Database initialized
[main] INFO: Pre-loading Whisper model...
[main] INFO: Loaded modules: meetings, weather, whoop
[main] INFO: WhatsApp HTTP server will start on port 8787
[main] INFO: Bot starting...
```

Now open Telegram, find the bot you created, send `/start`. You should
get a greeting back.

Try a few things:

- "What's on my calendar today?"
- "Add a todo: buy groceries"
- "Remember that my niece's name is Maya."
- "What do you know about me?"

If everything works, you have a personal AI assistant. Move on to
making it permanent.

---

## Step 10 — Run as a service (optional)

While `python main.py` works, you probably want the bot to start
automatically and survive crashes/reboots.

### Windows — nssm

`setup_service.bat` registers MyGG as a Windows service via
[nssm](https://nssm.cc).

1. Install nssm: `winget install nssm.nssm` (or download from
   https://nssm.cc and add to PATH).
2. Right-click on PowerShell, **Run as administrator**.
3. From the project root:
   ```powershell
   .\setup_service.bat
   ```
4. Answer **Y** to start the service immediately.

The service is named **MyGG**. Logs go to `.\logs\mygg.log`. Common
commands:

```powershell
nssm start MyGG
nssm stop MyGG
nssm restart MyGG
nssm status MyGG
nssm remove MyGG confirm
```

### macOS — launchd

Create `~/Library/LaunchAgents/com.mygg.bot.plist` with the equivalent of:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.mygg.bot</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOU/MyGG/venv/bin/python</string>
    <string>/Users/YOU/MyGG/main.py</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/YOU/MyGG</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/YOU/MyGG/logs/mygg.log</string>
  <key>StandardErrorPath</key><string>/Users/YOU/MyGG/logs/mygg-error.log</string>
</dict>
</plist>
```

Load it with `launchctl load ~/Library/LaunchAgents/com.mygg.bot.plist`.

### Linux — systemd

`/etc/systemd/system/mygg.service`:

```ini
[Unit]
Description=MyGG Personal AI Assistant
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/you/MyGG
ExecStart=/home/you/MyGG/venv/bin/python /home/you/MyGG/main.py
Restart=on-failure
RestartSec=5
User=you

[Install]
WantedBy=multi-user.target
```

Then `sudo systemctl daemon-reload && sudo systemctl enable --now mygg`.

> **Don't forget:** Qdrant (Docker) and Ollama also need to start at
> boot. Docker Desktop and Ollama both have "start on login" options in
> their settings; turn them on.

---

## Step 11 — Optional features

### WHOOP

If you wear a WHOOP and want sleep/recovery/strain in your morning briefings:

1. Register an app at https://developer.whoop.com.
2. Set the redirect URI to `http://localhost:8788/callback`.
3. Put the client ID and secret into `.env`:
   ```env
   WHOOP_CLIENT_ID=...
   WHOOP_CLIENT_SECRET=...
   WHOOP_REDIRECT_URI=http://localhost:8788/callback
   ```
4. Run the OAuth helper once:
   ```bash
   python whoop_auth.py
   ```
5. Restart MyGG. The morning briefing will now include recovery + sleep.

### WhatsApp (read-only)

Lets MyGG search your WhatsApp messages and summarize chats.

1. Install Node.js 20+.
2. From the `whatsapp-bridge/` directory:
   ```bash
   cd whatsapp-bridge
   npm install
   ```
3. Pick a long random string for `WHATSAPP_BRIDGE_SECRET` and put it in
   `.env` of the **MyGG project root** *and* in `whatsapp-bridge/.env`
   (create it if needed) — both sides need the same value.
4. Start the bridge:
   ```bash
   node index.js
   ```
5. The first run prints a QR code (or saves `qr.png`). Open WhatsApp on
   your phone → Settings → Linked Devices → Link Device, scan it.
6. Restart MyGG. Try: "summarize my WhatsApp from the last 12 hours".

> **Important:** the bridge must keep running for messages to flow in.
> Consider running it via `nssm`, `pm2`, or a systemd service alongside
> the main bot.

### Weather (OpenWeatherMap)

1. Sign up at https://openweathermap.org/api.
2. Copy your API key into `.env` as `OPENWEATHER_API_KEY`.
3. Optionally set `DEFAULT_LOCATION="City, ST"`.
4. Restart MyGG. The morning briefing now includes today's weather.

### Voice replies (TTS reference)

By default MyGG uses an Edge TTS voice (`en-IE-EmilyNeural` —
configurable via `TTS_VOICE`). No setup needed — voice replies just work.

If you want to clone a specific voice, you'll need a different TTS
backend; that's out of scope here, but `audio.py` is a good place to
swap in alternatives.

---

## Troubleshooting

The 10 most common reasons MyGG won't start, in rough order of frequency:

1. **`ConnectionError: ollama is not running`**
   Start Ollama. On Windows, search "Ollama" in the Start menu and launch
   the app. Verify with `curl http://localhost:11434/api/tags`.

2. **`Connection refused` on port 6333**
   Qdrant container isn't running. `docker start mygg-qdrant`. If the
   container doesn't exist, rerun the `docker run` command from
   [Step 4](#step-4--vector-database-qdrant).

3. **`401 Unauthorized` from Telegram**
   Wrong `TELEGRAM_BOT_TOKEN`. Copy-paste it again from BotFather.

4. **`401 Unauthorized` from Anthropic**
   Wrong `ANTHROPIC_API_KEY`. Make sure there are no quotes or
   trailing spaces around the value in `.env`.

5. **Google OAuth says "redirect URI mismatch"**
   In your Google Cloud Console, go to your OAuth client → Authorized
   redirect URIs and add `http://localhost:8090/`. (`setup_calendar.py`
   uses port 8090 by default.)

6. **Bot says nothing back when I message it**
   Check `ALLOWED_USER_IDS`. Send `/start` to **@userinfobot** to confirm
   your real ID. The value in `.env` must match exactly.

7. **`Whoop API 401`**
   Token expired and refresh failed. Run `python whoop_auth.py` again.

8. **`ModuleNotFoundError: No module named 'ollama'`** (or `mem0` etc.)
   You forgot `pip install -r requirements.txt`, or you have multiple
   Python installations. Make sure your venv is activated — the prompt
   should start with `(venv)`.

9. **Service install says "permission denied"**
   `setup_service.bat` (Windows) needs an Administrator PowerShell.
   Right-click PowerShell → Run as administrator. On Linux, prefix
   `systemctl` commands with `sudo`.

10. **WhatsApp QR code expired before I could scan it**
    Stop the bridge (`Ctrl+C`), restart `node index.js`, scan within
    20 seconds. If the auth state got into a weird place, delete
    `whatsapp-bridge/auth_state/` and try again.

If you hit something not on this list, the logs in `logs/mygg.log` (or
the terminal output if you're running `python main.py` directly) will
usually tell you exactly which integration is broken.

---

## Getting help

- **Stuck during install?** Open the entire MyGG folder in Claude Code
  (https://claude.com/claude-code) and ask: "Help me debug why MyGG
  won't start. Here are the logs: ...". Claude Code is genuinely good at
  walking through this kind of integration setup.
- **Bug?** Open a GitHub issue with the relevant log lines.
- **Idea?** Open a feature-request issue.
- **Security concern?** See [SECURITY.md](SECURITY.md).

Welcome to MyGG. Once it knows you, you won't want to live without it.
