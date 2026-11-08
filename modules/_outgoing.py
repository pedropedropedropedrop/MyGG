"""One expiring approval per private chat. Keep the reviewed payload and delivery receipt."""
import json
import secrets
import time
from contextlib import closing

import tools
from constants import DRAFT_TTL_SECONDS


def _db():
    conn = tools.get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS outgoing_drafts (
        chat_id INTEGER PRIMARY KEY, token TEXT NOT NULL, channel TEXT NOT NULL,
        preview TEXT NOT NULL, payload TEXT NOT NULL, expires_at REAL NOT NULL,
        status TEXT NOT NULL, receipt TEXT NOT NULL DEFAULT '')""")
    return conn


def prepare(chat_id: int, channel: str, preview: str, payload: dict) -> str:
    if chat_id <= 0 or channel not in {"email", "whatsapp", "calendar"}:
        raise ValueError("Unsupported approval channel")
    token = secrets.token_hex(12)
    with closing(_db()) as conn, conn:
        conn.execute("""INSERT OR REPLACE INTO outgoing_drafts
            (chat_id, token, channel, preview, payload, expires_at, status)
            VALUES (?, ?, ?, ?, ?, ?, 'preparing')""",
            (chat_id, token, channel, preview, json.dumps(payload), time.time() + DRAFT_TTL_SECONDS))
    return token


def presented(chat_id: int, token: str):
    with closing(_db()) as conn, conn:
        conn.execute("UPDATE outgoing_drafts SET status='pending' WHERE chat_id=? AND token=? AND status='preparing'",
                     (chat_id, token))


def get_outgoing_context(chat_id: int) -> dict | None:
    with closing(_db()) as conn:
        row = conn.execute("SELECT channel, preview, status, receipt, expires_at FROM outgoing_drafts WHERE chat_id=?",
                           (chat_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    if result.pop("expires_at") <= time.time() and result["status"] in {"pending", "preparing"}:
        result["status"] = "expired"
    return result


async def resolve(chat_id: int, token: str, action: str) -> str:
    if action not in {"send", "cancel"}:
        return "Invalid approval."
    status = "sending" if action == "send" else "cancelled"
    # Claim before network I/O so a restart or replay cannot send twice.
    with closing(_db()) as conn, conn:
        row = conn.execute("""UPDATE outgoing_drafts SET status=? WHERE chat_id=? AND token=?
            AND status='pending' AND expires_at>? RETURNING channel, payload""",
            (status, chat_id, token, time.time())).fetchone()
    if row is None:
        return "This draft is no longer pending."
    if action == "cancel":
        return "Draft cancelled."
    payload = json.loads(row["payload"])
    try:
        if row["channel"] == "email":
            result = await tools._deliver_email(payload)
        elif row["channel"] == "whatsapp":
            result = await tools._deliver_whatsapp(payload)
        else:
            result = await tools.create_event(**payload)
        status = "sent" if result.startswith(("Email sent from ", "Sent to ", "Event created:")) else "failed"
    except Exception:
        status = "unknown"
        result = "Delivery status is unknown. Check the destination before trying again."
    with closing(_db()) as conn, conn:
        conn.execute("UPDATE outgoing_drafts SET status=?, receipt=? WHERE chat_id=? AND token=?",
                     (status, result, chat_id, token))
    return result
