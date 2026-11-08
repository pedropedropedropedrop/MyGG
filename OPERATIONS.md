# Run GG unattended

Start with a working private chat and `python validate.py`. Use the terminal and Python environment from the [installation guide](ONBOARDING.md#install-mygg). Each MyGG folder supports one bot process. Stop its foreground copy or service before switching to the supervisor.

## Supervise the bot

From the project folder run `python supervise.py`. The supervisor launches GG, watches that child and restarts it after a crash. It writes rotating logs to `logs/supervisor.log`. Use Ctrl+C to stop the terminal supervisor or run `python supervise.py --stop` from another terminal in the same folder. Restart the supervisor after changing `.env`.

Add only the options you want:

| Option | What it does |
| --- | --- |
| `--bridge` | Starts and watches this installation's Node WhatsApp bridge. Complete [WhatsApp setup](ONBOARDING.md#whatsapp) first. |
| `--updates` | Every five minutes, checks the current Git branch's configured upstream. The next section describes validation and recovery. Requires a Git clone. |
| `--ollama` | Starts a dedicated Ollama server for this copy and can restart it after repeated inference timeouts. See [Dedicated Ollama](#dedicated-ollama). |

For example, `python supervise.py --bridge --updates` supervises the bot and WhatsApp with updates enabled. On macOS/Linux, an existing service manager can launch the same command with absolute paths to the virtual environment's Python and `supervise.py`.

### Start at Windows sign-in

1. Test the supervisor in a terminal and stop it. Open PowerShell in your MyGG folder.
2. Run `.\install-watchdog.ps1`. Optional switches are `-Bridge`, `-Updates` and `-Ollama`, for example `.\install-watchdog.ps1 -Bridge`.
3. The installer registers a task for your current Windows user and prints a `Start-ScheduledTask` command for starting immediately. Otherwise it starts on your next sign-in. Read its logs in `logs/supervisor.log`.

To stop and remove the sign-in task, run `.\install-watchdog.ps1 -Remove`. A Windows service through NSSM is also available for boot-time startup. Choose one of these methods per installation. [Windows service setup](ONBOARDING.md#keep-gg-running)

## Checked updates

Automatic updates fetch from the current branch's existing Git remote. On a clean branch with an available fast-forward, the supervisor extracts the candidate source to a temporary folder, checks Python syntax and runs `validate.py` there with offline test settings. Once it has verified the candidate and rechecked local edits, it stops its own app processes, updates the checkout, and reruns validation with your configuration. If that fails on the unchanged checkout, it restores the previous revision. The supervisor restarts its bot and bridge.

An edited or diverged branch stays at its existing version. If live validation fails, the supervisor skips that revision until you restart it or a newer revision arrives. Changed dependency manifests require a [manual update](ONBOARDING.md#update-an-existing-installation) so you can install the new packages. A ZIP installation uses the same manual guide. An update only touches version-controlled source. Keep private backups before upgrading, especially across database format changes. Changes to the supervisor program itself take effect the next time you launch it.

## Dedicated Ollama

You can use your regular Ollama app with MyGG without enabling supervision of Ollama. To give this instance its own recoverable server:

1. Install Ollama and download the models in your user account. Set `LOCAL_BASE_URL=http://127.0.0.1:11435` in `.env` and select the local model. Pick an unused loopback port other than the usual 11434. If the same instance will power long-term memory, also set `OLLAMA_BASE_URL=http://127.0.0.1:11435`.
2. Start `python supervise.py --ollama`. The supervisor starts `ollama serve` bound to that dedicated port. Only the process it starts is eligible for recovery. Two actual inference timeouts within fifteen minutes can trigger a restart. A successful inference clears the timeout state. Merely waiting for a busy model does not trigger recovery.
3. If you want GG's model to stay loaded, set `MYGG_PIN_MODEL=true` and restart. GG requests a keep-alive at startup and every 30 minutes. For optional memory warmth, use `MYGG_WARM_MEMORY=true` with `ENABLE_MEMORY=true`. Pinning consumes memory while the model is idle.

Run Docker and your saved Qdrant container when memory is enabled. On Windows, `start-gg.ps1` can start configured local services for foreground use. With a supervisor or service, start Docker on login and keep the Qdrant restart policy from the [memory setup](ONBOARDING.md#long-term-memory).

## Health notices and recovery

Set `OWNER_CHAT_ID` to your own allowlisted Telegram ID. GG probes connected Google accounts, WHOOP, WhatsApp, and the Ollama/Qdrant services you have enabled. It sends changed states, a first failure, and a Monday digest. Checks run about two minutes after startup and daily at 8 a.m. in `TIMEZONE`. If Telegram delivery fails, the previous state is kept so the next probe can retry.

For a disconnected Google or WHOOP session, repeat its authorization step. For WhatsApp, check **Linked devices** on your phone and read the bridge log. To recover memories on a new computer, restore your own Qdrant backup, original collection and model settings. For a source update rollback, preserve your data and consult `git reflog` for the previously working revision. [Per-feature setup and backups](ONBOARDING.md#update-an-existing-installation)
