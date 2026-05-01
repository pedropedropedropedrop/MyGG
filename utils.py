"""Shared utilities for GG."""

from constants import TELEGRAM_MAX_LENGTH


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
