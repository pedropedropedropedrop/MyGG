"""Check an installation and its settings locally before starting Telegram."""
import os
import sys
import tempfile
from importlib.util import find_spec
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROJECT_DIR = Path(__file__).resolve().parent


def main() -> int:
    try:
        from dotenv import load_dotenv
        from telegram import Bot
        from telegram.error import InvalidToken
    except ImportError as exc:
        print(f"Missing {exc.name}. Install requirements.txt with this Python.", file=sys.stderr)
        return 1

    load_dotenv(PROJECT_DIR / ".env")
    try:
        from utils import validate_access
        allowed, owner = validate_access()
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("Set TELEGRAM_BOT_TOKEN in .env with the token from BotFather.")
        try:
            Bot(token)
        except InvalidToken:
            raise ValueError("Check TELEGRAM_BOT_TOKEN in .env against the token from BotFather.") from None
        backend = os.getenv("LLM_BACKEND", "anthropic").strip().lower()
        if backend not in {"anthropic", "local"}:
            raise ValueError("Choose anthropic or local for LLM_BACKEND.")
        if backend == "anthropic" and not os.getenv("ANTHROPIC_API_KEY", "").strip():
            raise ValueError("Set ANTHROPIC_API_KEY in .env for Claude, or configure the local backend.")
        try:
            ZoneInfo(os.getenv("TIMEZONE", "America/New_York"))
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Set TIMEZONE to an IANA name such as America/New_York.") from None

        import tools
        if tools.memory_enabled() and any(find_spec(p) is None for p in ("mem0", "ollama")):
            raise ValueError("Install requirements/memory.txt or set ENABLE_MEMORY=false.")
        # Discovery can initialize module tables. Test it against a disposable database.
        import modules
        with tempfile.TemporaryDirectory() as tmp:
            tools.DB_PATH = Path(tmp) / "check.db"
            tools.init_db()
            import main as bot
            bot.ALLOWED_USER_IDS, bot.heartbeat.OWNER_CHAT_ID = allowed, owner
            modules.discover()
            missing = {"meetings", "weather", "whoop"} - set(modules.get_module_names())
            if missing:
                raise ValueError("Could not load these built-in modules: " + ", ".join(sorted(missing)))
            from telegram.ext import Application
            app = Application.builder().token(token).build()
            bot._register_handlers(app)
            bot._schedule_jobs(app, tools.TZ)
    except (ValueError, ImportError, RuntimeError) as exc:
        print(f"Setup check failed: {exc}", file=sys.stderr)
        return 1

    print(f"Setup check passed ({backend}). Start MyGG with this Python and main.py.")
    print("This offline check does not sign in to Telegram, the model, or optional services.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
