# Security Policy

MyGG is a self-hosted personal tool, not a multi-tenant service. The
threat model is shaped accordingly: it assumes you control the machine
it runs on and that you treat your `.env` and OAuth tokens like
passwords.

## Reporting a vulnerability

If you discover a security issue, please **do not file a public GitHub
issue.** Instead, email the maintainers (replace `EMAIL@EXAMPLE.COM`
in your fork with the real contact, or use GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
feature).

Please include:

- A description of the vulnerability and the components it affects.
- Steps to reproduce.
- The impact (data leak, privilege escalation, RCE, etc.).
- Whether you're willing to be credited in the fix advisory.

We aim to acknowledge reports within 5 business days.

## Threat model (self-hosted)

The bot is exposed to the world only through:

- The Telegram chat — gated by `ALLOWED_USER_IDS`.
- The local HTTP receiver on `127.0.0.1:8787` (WhatsApp bridge) —
  gated by a shared `WHATSAPP_BRIDGE_SECRET`.
- The localhost OAuth callbacks during initial setup (Google: 8090,
  WHOOP: 8788) — only briefly open during `setup_calendar.py` /
  `whoop_auth.py`.

What you must protect:

| Asset                       | Where it lives                       | If leaked, attacker can…                          |
| --------------------------- | ------------------------------------ | ------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN`        | `.env`                               | Impersonate your bot, intercept future messages.  |
| `ANTHROPIC_API_KEY`         | `.env`                               | Spend your Anthropic credit; access your data.    |
| `token.json` / `token_*.json` | Project root                       | Read/send your Gmail; create calendar events.     |
| `credentials.json`          | Project root                         | Help re-authorize if combined with a code grab.   |
| `.whoop_token.json`         | Project root                         | Read your WHOOP data.                             |
| `WHATSAPP_BRIDGE_SECRET`    | `.env`                               | Inject fake WhatsApp messages into your DB.       |
| `pa.db`                     | Project root                         | Read your todos, reminders, WhatsApp mirror.      |
| `qdrant_storage/`           | Project root                         | Read everything the assistant has remembered.     |

`ALLOWED_USER_IDS` is the most important defense. With it set correctly,
even if someone discovers your bot's username they cannot interact with
it.

## Common mistakes to avoid

1. **Committing `.env` or `token*.json`.** The shipped `.gitignore`
   covers them, but double-check `git status` before your first push.
2. **Leaving `ALLOWED_USER_IDS` empty.** The bot will accept any user.
3. **Exposing the WhatsApp bridge port to the network.** It binds to
   `127.0.0.1` by default; do not change that unless you put a real
   reverse proxy + auth in front of it.
4. **Sharing the OAuth `credentials.json`** — it's not as sensitive as
   `token.json`, but it's still tied to your Google project.
5. **Running the bot on a shared machine** — anyone with read access to
   the project directory has read access to your tokens.

## Supported versions

MyGG is a personal tool with no LTS commitment. Only the latest commit
on `main` is supported. If you fork it, you own your fork's security.

## Out of scope

- Multi-user / multi-tenant deployments (MyGG is single-user by design).
- Network-exposed bot deployments (always run behind Telegram's TLS;
  never expose the HTTP receiver publicly).
- Local privilege escalation on your own machine (assume the user
  running the bot is trusted).
