"""Shared utilities for GG."""

import os
import tempfile
from pathlib import Path

from constants import TELEGRAM_MAX_LENGTH


def write_secret_file(path: Path, text: str) -> None:
    """Replace a token file atomically using a private temporary file."""
    tmp = tempfile.NamedTemporaryFile(dir=path.parent, prefix=".mygg-secret-",
                                      mode="w", encoding="utf-8", delete=False)
    temporary = Path(tmp.name)
    try:
        with tmp:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def split_long_message(text: str) -> list[str]:
    """Split a message into chunks that fit Telegram's limit."""
    if len(text) <= TELEGRAM_MAX_LENGTH:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(line) > TELEGRAM_MAX_LENGTH:
            # Long line — flush current, then chunk the line without dropping content
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(line), TELEGRAM_MAX_LENGTH):
                chunks.append(line[i:i + TELEGRAM_MAX_LENGTH])
        elif len(current) + len(line) + 1 > TELEGRAM_MAX_LENGTH:
            if current:
                chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)

    return chunks


def validate_access() -> tuple[set[int], int]:
    """Read the authorized private chat IDs and optional briefing recipient."""
    entries = [s.strip() for s in os.getenv("ALLOWED_USER_IDS", "").split(",") if s.strip()]
    if not entries or any(not s.isascii() or not s.isdecimal() or int(s) <= 0 for s in entries):
        raise ValueError("Set ALLOWED_USER_IDS to positive Telegram user IDs in .env")
    allowed = {int(s) for s in entries}
    owner = os.getenv("OWNER_CHAT_ID", "").strip()
    if owner and (not owner.isascii() or not owner.isdecimal() or int(owner) not in allowed):
        raise ValueError("Set OWNER_CHAT_ID to an ID in ALLOWED_USER_IDS, or leave it blank")
    return allowed, int(owner) if owner else 0
