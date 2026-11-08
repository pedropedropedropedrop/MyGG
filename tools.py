"""Tool implementations for the MyGG personal AI assistant."""

import os
import json
import sqlite3
import asyncio
import threading
import logging
import re
from importlib.util import find_spec
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from utils import write_secret_file

from constants import (
    EMAIL_BODY_MAX_CHARS,
    EMAIL_DRAFT_MAX_CHARS,
    MEMORY_RECALL_TIMEOUT_SECONDS,
    TRANSCRIBE_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "pa.db"
TZ = ZoneInfo(os.getenv("TIMEZONE", "America/New_York"))
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

GOOGLE_ACCOUNTS = {
    "personal": {
        "token_path": Path(__file__).parent / "token.json",
        "label": os.getenv("GOOGLE_ACCOUNT_LABEL", "Personal Google account"),
    },
}
for account_name in os.getenv("GOOGLE_EXTRA_ACCOUNTS", "").split(","):
    account_name = account_name.strip()
    if not account_name or account_name in GOOGLE_ACCOUNTS:
        continue
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", account_name):
        raise ValueError("GOOGLE_EXTRA_ACCOUNTS needs lowercase names such as work,club.")
    GOOGLE_ACCOUNTS[account_name] = {
        "token_path": Path(__file__).with_name(f"token_{account_name}.json"),
        "label": os.getenv(f"GOOGLE_ACCOUNT_{account_name.upper()}_LABEL", account_name),
    }


# ── Database ─────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task TEXT NOT NULL,
                priority TEXT DEFAULT 'medium',
                due_date TEXT,
                project TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                due_at TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                notified INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS whatsapp_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                whatsapp_message_id TEXT UNIQUE,
                chat_jid TEXT NOT NULL,
                chat_name TEXT,
                sender_jid TEXT NOT NULL,
                sender_name TEXT,
                message_text TEXT,
                message_type TEXT DEFAULT 'text',
                is_from_me INTEGER DEFAULT 0,
                timestamp TEXT NOT NULL,
                summarized INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_wa_timestamp ON whatsapp_messages(timestamp);
            CREATE INDEX IF NOT EXISTS idx_wa_chat ON whatsapp_messages(chat_jid);
            CREATE INDEX IF NOT EXISTS idx_wa_summarized ON whatsapp_messages(summarized);
            CREATE TABLE IF NOT EXISTS meeting_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                date TEXT NOT NULL,
                duration_seconds REAL,
                language TEXT,
                calendar_event_id TEXT,
                calendar_account TEXT,
                transcript_path TEXT,
                summary TEXT,
                decisions TEXT,
                action_items TEXT,
                participants TEXT,
                clarifications TEXT,
                status TEXT DEFAULT 'processing',
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.commit()
    finally:
        conn.close()


# ── Memory (mem0) ────────────────────────────────────────────────────────────

_memory_client = None
_memory_lock = threading.Lock()


def memory_enabled() -> bool:
    # Older installations bundled mem0 and have no switch in their .env.
    value = os.getenv("ENABLE_MEMORY")
    return find_spec("mem0") is not None if value is None else value.lower() == "true"


def _get_memory():
    global _memory_client
    if _memory_client is not None:
        return _memory_client
    with _memory_lock:
        if _memory_client is not None:
            return _memory_client
        os.environ["MEM0_TELEMETRY"] = "False"
        from mem0 import Memory
        config = {
            "llm": {
                "provider": "ollama",
                "config": {
                    "model": os.getenv("OLLAMA_MEMORY_MODEL", "qwen3:8b"),
                    "ollama_base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                },
            },
            "embedder": {
                "provider": "ollama",
                "config": {
                    "model": os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
                    "ollama_base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                },
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": os.getenv("MEMORY_COLLECTION", "mygg_memories"),
                    "host": os.getenv("QDRANT_HOST", "localhost"),
                    "port": int(os.getenv("QDRANT_PORT", "6333")),
                    "embedding_model_dims": 768,
                },
            },
            "version": "v1.1",
        }
        _memory_client = Memory.from_config(config)
    return _memory_client


def _memory_user_id() -> str:
    """Logical user-id mem0 partitions memories by. Defaults to USER_NAME (lowercased)."""
    return os.getenv("USER_NAME", "user").strip().lower() or "user"


async def remember(text: str, tags: list[str] | None = None, *, infer: bool = True) -> str:
    if not memory_enabled():
        return "Long-term memory is turned off. See the memory setup in ONBOARDING.md."
    metadata = {}
    if tags:
        metadata["tags"] = ",".join(tags)
    metadata["stored_at"] = datetime.now(TZ).isoformat()

    def _add():
        m = _get_memory()
        return m.add(text, user_id=_memory_user_id(), metadata=metadata, infer=infer)

    result = await asyncio.to_thread(_add)
    rows = result.get("results", []) if isinstance(result, dict) else result
    if any(isinstance(row, dict) and row.get("id") and row.get("event") in {"ADD", "UPDATE"}
           for row in (rows or [])):
        return f"Stored: {text}"
    if infer:
        return "No new long-term memory was recorded."
    raise RuntimeError("The memory store did not confirm saving the summary.")


async def recall(query: str) -> str:
    if not memory_enabled():
        return "No relevant memories found."
    def _search():
        m = _get_memory()
        return m.search(query, user_id=_memory_user_id(), limit=10)

    results = await asyncio.to_thread(_search)
    hits = results.get("results", []) if isinstance(results, dict) else results
    if not hits:
        return "No relevant memories found."
    return "\n".join(f"- {r['memory']}" for r in hits)


# ── Google Services (multi-account) ─────────────────────────────────────────

_services: dict[str, dict] = {}  # {account_name: {"calendar": ..., "gmail": ...}}


def _get_google_creds(account: str):
    """Get credentials for a Google account, refreshing if expired."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    acct = GOOGLE_ACCOUNTS.get(account)
    if not acct or not acct["token_path"].exists():
        return None

    creds = Credentials.from_authorized_user_file(str(acct["token_path"]), GOOGLE_SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        write_secret_file(acct["token_path"], creds.to_json())
        # Invalidate cached services — they hold the old credentials
        _services.pop(account, None)
    return creds


def _get_calendar_service(account: str = "personal"):
    if account in _services and "calendar" in _services[account]:
        return _services[account]["calendar"]

    from googleapiclient.discovery import build
    creds = _get_google_creds(account)
    if not creds:
        return None

    service = build("calendar", "v3", credentials=creds)
    _services.setdefault(account, {})["calendar"] = service
    return service


def _get_gmail_service(account: str = "personal"):
    if account in _services and "gmail" in _services[account]:
        return _services[account]["gmail"]

    from googleapiclient.discovery import build
    creds = _get_google_creds(account)
    if not creds:
        return None

    service = build("gmail", "v1", credentials=creds)
    _services.setdefault(account, {})["gmail"] = service
    return service


def _available_accounts() -> list[str]:
    """Return account names that have token files."""
    return [name for name, cfg in GOOGLE_ACCOUNTS.items() if cfg["token_path"].exists()]


# ── Google Calendar ──────────────────────────────────────────────────────────


def _format_calendar_events(events: list, num_accounts: int) -> list[str]:
    """Format calendar events into human-readable lines."""
    lines = []
    current_date = None
    for event in events:
        raw = event["start"].get("dateTime", event["start"].get("date", ""))
        summary = event.get("summary", "(no title)")
        acct_tag = f" [{event['_account']}]" if num_accounts > 1 else ""
        if "T" in raw:
            dt = datetime.fromisoformat(raw)
            day_str = dt.strftime("%a %b %d")
            if day_str != current_date:
                current_date = day_str
                lines.append(f"\n{day_str}:")
            lines.append(f"  {dt.strftime('%H:%M')} - {summary}{acct_tag}")
        else:
            lines.append(f"  All day - {summary}{acct_tag}")
    return lines


async def get_calendar(date: str | None = None, days: int = 1, account: str | None = None) -> str:
    def _get():
        accounts = [account] if account else _available_accounts()
        if not accounts:
            return "No calendar accounts configured. Run: python setup_calendar.py"

        if date:
            start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=TZ)
        else:
            start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)

        end = start + timedelta(days=days)

        all_events = []
        for acct in accounts:
            service = _get_calendar_service(acct)
            if not service:
                continue
            label = GOOGLE_ACCOUNTS[acct]["label"]
            try:
                events_result = (
                    service.events()
                    .list(
                        calendarId="primary",
                        timeMin=start.isoformat(),
                        timeMax=end.isoformat(),
                        singleEvents=True,
                        orderBy="startTime",
                    )
                    .execute()
                )
                for event in events_result.get("items", []):
                    event["_account"] = acct
                    event["_label"] = label
                    all_events.append(event)
            except Exception as e:
                all_events.append({"_error": f"{label}: {e}"})

        if not all_events:
            return f"No events for {start.strftime('%a %b %d')}." + (
                f" ({days} days)" if days > 1 else ""
            )

        # Sort by start time
        def sort_key(ev):
            raw = ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", ""))
            return raw

        errors = [e for e in all_events if "_error" in e]
        events = sorted([e for e in all_events if "_error" not in e], key=sort_key)

        lines = []
        if errors:
            for e in errors:
                lines.append(f"⚠ {e['_error']}")

        lines.extend(_format_calendar_events(events, len(accounts)))
        return "\n".join(lines)

    return await asyncio.to_thread(_get)


async def create_event(
    title: str, start: str, end: str | None = None, description: str | None = None,
    account: str = "personal", attendees: list[str] | None = None,
) -> str:
    def _create():
        service = _get_calendar_service(account)
        if not service:
            return f"Calendar not configured for '{account}'. Run: python setup_calendar.py {account}"

        tz_name = str(TZ)
        start_dt = datetime.fromisoformat(start)
        end_dt = (
            datetime.fromisoformat(end)
            if end
            else start_dt + timedelta(hours=1)
        )

        event = {
            "summary": title,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": tz_name},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": tz_name},
        }
        if description:
            event["description"] = description
        if attendees:
            event["attendees"] = [{"email": e} for e in attendees]

        service.events().insert(calendarId="primary", body=event,
                                sendUpdates="all" if attendees else "none").execute()
        label = GOOGLE_ACCOUNTS.get(account, {}).get("label", account)
        invited = f" (invited: {', '.join(attendees)})" if attendees else ""
        return f"Event created: {title} at {start_dt.strftime('%a %b %d %H:%M')} [{label}]{invited}"

    return await asyncio.to_thread(_create)


async def get_upcoming_events(minutes: int = 20) -> list[dict]:
    """Return raw calendar events starting within the next `minutes` minutes, across all accounts."""
    def _get():
        now = datetime.now(TZ)
        end = now + timedelta(minutes=minutes)
        results = []
        for acct in _available_accounts():
            service = _get_calendar_service(acct)
            if not service:
                continue
            try:
                events_result = (
                    service.events()
                    .list(
                        calendarId="primary",
                        timeMin=now.isoformat(),
                        timeMax=end.isoformat(),
                        singleEvents=True,
                        orderBy="startTime",
                    )
                    .execute()
                )
                for event in events_result.get("items", []):
                    event["_account"] = acct
                    results.append(event)
            except Exception:
                logger.warning(f"Failed to fetch upcoming events for account '{acct}'", exc_info=True)
        return results
    return await asyncio.to_thread(_get)


# ── Gmail ────────────────────────────────────────────────────────────────────


def _parse_email_headers(headers: list) -> dict:
    result = {}
    for h in headers:
        name = h["name"].lower()
        if name in ("from", "to", "subject", "date"):
            result[name] = h["value"]
    return result


def _get_email_body(payload: dict) -> str:
    """Extract plain text body from Gmail message payload."""
    import base64

    # Simple message
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    # Multipart — look for text/plain
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        # Nested multipart
        for sub in part.get("parts", []):
            if sub.get("mimeType") == "text/plain" and sub.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(sub["body"]["data"]).decode("utf-8", errors="replace")

    return "(no text body)"


async def list_emails(query: str = "is:inbox", max_results: int = 10, account: str | None = None) -> str:
    def _list():
        accounts = [account] if account else _available_accounts()
        if not accounts:
            return "No email accounts configured. Run: python setup_calendar.py"

        all_lines = []
        for acct in accounts:
            service = _get_gmail_service(acct)
            if not service:
                continue
            label = GOOGLE_ACCOUNTS[acct]["label"]

            try:
                results = (
                    service.users()
                    .messages()
                    .list(userId="me", q=query, maxResults=max_results)
                    .execute()
                )
                messages = results.get("messages", [])
                if not messages:
                    continue

                if len(accounts) > 1:
                    all_lines.append(f"── {label} ──")

                for msg_ref in messages:
                    msg = (
                        service.users()
                        .messages()
                        .get(userId="me", id=msg_ref["id"], format="metadata",
                             metadataHeaders=["From", "Subject", "Date"])
                        .execute()
                    )
                    headers = _parse_email_headers(msg.get("payload", {}).get("headers", []))
                    snippet = msg.get("snippet", "")[:100]
                    unread = "UNREAD" in msg.get("labelIds", [])
                    marker = "[NEW] " if unread else ""
                    acct_tag = f" [{acct}]" if len(accounts) > 1 else ""
                    all_lines.append(
                        f"  {marker}{acct}:{msg_ref['id']}\n"
                        f"    From: {headers.get('from', '?')}\n"
                        f"    Subject: {headers.get('subject', '(no subject)')}\n"
                        f"    {snippet}"
                    )
            except Exception as e:
                all_lines.append(f"⚠ {label}: {e}")

        if not all_lines:
            return f"No emails found for: {query}"
        return "\n\n".join(all_lines)

    return await asyncio.to_thread(_list)


def _parse_email_id(email_id: str) -> tuple[str, str]:
    """Parse 'account:id' format. Falls back to personal if no prefix."""
    if ":" in email_id:
        parts = email_id.split(":", 1)
        if parts[0] in GOOGLE_ACCOUNTS:
            return parts[0], parts[1]
    return "personal", email_id


async def read_email(email_id: str) -> str:
    def _read():
        acct, msg_id = _parse_email_id(email_id)
        service = _get_gmail_service(acct)
        if not service:
            return f"Gmail not configured for '{acct}'."

        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_id, format="full")
            .execute()
        )
        headers = _parse_email_headers(msg.get("payload", {}).get("headers", []))
        body = _get_email_body(msg.get("payload", {}))

        # Truncate very long emails
        if len(body) > EMAIL_BODY_MAX_CHARS:
            body = body[:EMAIL_BODY_MAX_CHARS] + "\n\n... (truncated)"

        label = GOOGLE_ACCOUNTS.get(acct, {}).get("label", acct)
        return (
            f"Account: {label}\n"
            f"From: {headers.get('from', '?')}\n"
            f"To: {headers.get('to', '?')}\n"
            f"Date: {headers.get('date', '?')}\n"
            f"Subject: {headers.get('subject', '(no subject)')}\n"
            f"\n{body}"
        )

    return await asyncio.to_thread(_read)


def _email_header(value: str) -> str:
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Invalid email header. Nothing sent.")
    return value


def _email_address(value: str) -> str:
    import re
    from email import policy

    header = policy.default.header_factory("To", _email_header(value))
    if header.defects or len(header.addresses) != 1:
        raise ValueError("One valid email address is required. Nothing sent.")
    address = header.addresses[0]
    labels = address.domain.split(".")
    if (not address.username or len(labels) < 2 or
            any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label) for label in labels)):
        raise ValueError("A valid email address is required. Nothing sent.")
    return address.addr_spec


def _prepare_email(to: str, subject: str, body: str, account: str = "personal", email_id: str | None = None) -> dict:
    """Read-only preparation: freeze every delivery field before showing approval."""
    import base64
    from email.mime.text import MIMEText

    if not isinstance(body, str) or not body.strip() or len(body) > EMAIL_DRAFT_MAX_CHARS:
        raise ValueError(f"An email body of 1–{EMAIL_DRAFT_MAX_CHARS} characters is required. Nothing sent.")
    if email_id is not None:
        if not isinstance(email_id, str) or not email_id.strip():
            raise ValueError("An email ID is required. Nothing sent.")
        if ":" in email_id:
            account, email_id = email_id.split(":", 1)
        if not email_id or any(char.isspace() for char in email_id):
            raise ValueError("Invalid email ID. Nothing sent.")
    if not isinstance(account, str) or account not in GOOGLE_ACCOUNTS:
        raise ValueError("Unknown email account. Nothing sent.")
    service = _get_gmail_service(account)
    if not service:
        raise ValueError(f"Gmail not configured for '{account}'. Nothing sent.")
    thread_id, in_reply_to, references = "", "", ""
    if email_id is not None:
        orig = service.users().messages().get(userId="me", id=email_id, format="metadata",
            metadataHeaders=["From", "Reply-To", "Subject", "Message-ID", "References"]).execute()
        headers = {header["name"].lower(): header["value"] for header in orig.get("payload", {}).get("headers", [])}
        to = headers.get("reply-to") or headers.get("from", "")
        thread_id = _email_header(orig.get("threadId", ""))
        in_reply_to = _email_header(headers.get("message-id", ""))
        if not thread_id or not in_reply_to:
            raise ValueError("Reply thread or Message-ID missing. Nothing sent.")
        references = _email_header(headers.get("references", ""))
        references = f"{references} {in_reply_to}".strip()
        subject = headers.get("subject", "")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
    recipient = _email_address(to)
    subject = _email_header(subject)
    from_address = _email_address(service.users().getProfile(userId="me").execute().get("emailAddress", ""))
    message = MIMEText(body, _charset="utf-8")
    message["From"], message["To"], message["Subject"] = from_address, recipient, subject
    if in_reply_to:
        message["In-Reply-To"], message["References"] = in_reply_to, references
    return {"account": account, "from_address": from_address, "recipient": recipient,
            "subject": subject, "text": body, "thread_id": thread_id,
            "raw": base64.urlsafe_b64encode(message.as_bytes()).decode()}


async def _deliver_email(draft: dict) -> str:
    """Only the trusted approval callback supplies a consumed, frozen draft."""
    def deliver():
        if draft["account"] not in GOOGLE_ACCOUNTS:
            raise ValueError("Unknown email account")
        service = _get_gmail_service(draft["account"])
        if not service:
            raise RuntimeError("Gmail account unavailable")
        sender = service.users().getProfile(userId="me").execute().get("emailAddress", "")
        if sender != draft["from_address"]:
            raise RuntimeError("Gmail account changed since approval was prepared")
        payload = {"raw": draft["raw"]}
        if draft["thread_id"]:
            payload["threadId"] = draft["thread_id"]
        sent = service.users().messages().send(userId="me", body=payload).execute()
        if not isinstance(sent, dict) or not sent.get("id"):
            raise RuntimeError("Gmail returned no delivery receipt")
        return f"Email sent from {draft['from_address']} ({draft['account']}) to {draft['recipient']} (Gmail receipt {sent['id']})"
    return await asyncio.to_thread(deliver)


# ── Todos ────────────────────────────────────────────────────────────────────


async def add_todo(
    task: str,
    priority: str = "medium",
    due_date: str | None = None,
    project: str | None = None,
) -> str:
    def _add():
        conn = get_db()
        try:
            cur = conn.execute(
                "INSERT INTO todos (task, priority, due_date, project) VALUES (?, ?, ?, ?)",
                (task, priority, due_date, project),
            )
            conn.commit()
            tid = cur.lastrowid
        finally:
            conn.close()
        parts = [f"Todo #{tid}: {task}"]
        if priority != "medium":
            parts.append(f"[{priority}]")
        if due_date:
            parts.append(f"(due: {due_date})")
        if project:
            parts.append(f"@{project}")
        return " ".join(parts)

    return await asyncio.to_thread(_add)


async def list_todos(
    project: str | None = None,
    status: str = "pending",
    priority: str | None = None,
) -> str:
    def _list():
        conn = get_db()
        try:
            query = "SELECT * FROM todos WHERE 1=1"
            params: list = []
            if status and status != "all":
                query += " AND status = ?"
                params.append(status)
            if project:
                query += " AND project = ?"
                params.append(project)
            if priority:
                query += " AND priority = ?"
                params.append(priority)
            query += " ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 END, id"
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()

        if not rows:
            return "No todos found."

        lines = []
        for r in rows:
            icon = "done" if r["status"] == "done" else "o"
            line = f"  [{icon}] #{r['id']} [{r['priority']}] {r['task']}"
            if r["project"]:
                line += f" @{r['project']}"
            if r["due_date"]:
                line += f" (due: {r['due_date']})"
            lines.append(line)
        return "\n".join(lines)

    return await asyncio.to_thread(_list)


async def complete_todo(todo_id: int) -> str:
    def _do():
        conn = get_db()
        try:
            cur = conn.execute(
                "UPDATE todos SET status = 'done' WHERE id = ? AND status = 'pending'",
                (todo_id,),
            )
            conn.commit()
            changed = cur.rowcount
        finally:
            conn.close()
        return f"Todo #{todo_id} completed." if changed else f"Todo #{todo_id} not found or already done."

    return await asyncio.to_thread(_do)


async def delete_todo(todo_id: int) -> str:
    def _do():
        conn = get_db()
        try:
            cur = conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
            conn.commit()
            changed = cur.rowcount
        finally:
            conn.close()
        return f"Todo #{todo_id} deleted." if changed else f"Todo #{todo_id} not found."

    return await asyncio.to_thread(_do)


# ── Web tools removed ────────────────────────────────────────────────────────
# web_search(), web_fetch(), web_crawl() replaced by Anthropic server-side tools:
# web_search_20260209, web_fetch_20260209 (defined in agent.py TOOL_DEFINITIONS)


# ── WhatsApp ────────────────────────────────────────────────────────────────


async def search_whatsapp(query: str, chat_name: str | None = None, hours_back: int = 24) -> str:
    """Search raw WhatsApp messages by text content."""
    def _search():
        conn = get_db()
        try:
            now = datetime.now(TZ)
            cutoff = (now - timedelta(hours=hours_back)).isoformat()

            sql = "SELECT * FROM whatsapp_messages WHERE timestamp >= ? AND message_text IS NOT NULL"
            params: list = [cutoff]

            if query:
                sql += " AND message_text LIKE ?"
                params.append(f"%{query}%")
            if chat_name:
                sql += " AND chat_name LIKE ?"
                params.append(f"%{chat_name}%")

            sql += " ORDER BY timestamp DESC LIMIT 50"
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        if not rows:
            return f"No WhatsApp messages matching '{query}' in the last {hours_back}h."

        lines = []
        for r in rows:
            sender = r["sender_name"] or r["sender_jid"]
            chat = r["chat_name"] or r["chat_jid"]
            ts = r["timestamp"][:16].replace("T", " ")
            direction = "You" if r["is_from_me"] else sender
            lines.append(f"[{ts}] {chat} — {direction}: {r['message_text']}")
        return "\n".join(lines)

    return await asyncio.to_thread(_search)


async def get_whatsapp_summary(chat_name: str | None = None, hours_back: int = 24) -> str:
    """Get a summary of recent WhatsApp activity."""
    def _get():
        conn = get_db()
        try:
            now = datetime.now(TZ)
            cutoff = (now - timedelta(hours=hours_back)).isoformat()

            if chat_name:
                sql = """SELECT chat_name, chat_jid, sender_name, sender_jid, message_text, is_from_me, timestamp
                         FROM whatsapp_messages
                         WHERE timestamp >= ? AND chat_name LIKE ? AND message_text IS NOT NULL
                         ORDER BY timestamp DESC LIMIT 100"""
                rows = conn.execute(sql, (cutoff, f"%{chat_name}%")).fetchall()
            else:
                sql = """SELECT chat_name, chat_jid, sender_name, sender_jid, message_text, is_from_me, timestamp
                         FROM whatsapp_messages
                         WHERE timestamp >= ? AND message_text IS NOT NULL
                         ORDER BY timestamp DESC LIMIT 100"""
                rows = conn.execute(sql, (cutoff,)).fetchall()
        finally:
            conn.close()

        if not rows:
            return f"No WhatsApp messages in the last {hours_back}h."

        # Group by chat
        chats: dict[str, list] = {}
        for r in rows:
            chat = r["chat_name"] or r["chat_jid"] or "Unknown"
            if chat not in chats:
                chats[chat] = []
            sender = "You" if r["is_from_me"] else (r["sender_name"] or r["sender_jid"])
            chats[chat].append(f"{sender}: {r['message_text']}")

        lines = []
        for chat, msgs in chats.items():
            lines.append(f"\n{chat} ({len(msgs)} messages):")
            # Show last 5 messages per chat in summary
            for m in msgs[:5]:
                lines.append(f"  {m}")
            if len(msgs) > 5:
                lines.append(f"  ... and {len(msgs) - 5} more")

        return "\n".join(lines)

    result = await asyncio.to_thread(_get)

    # Also pull older summarized context from mem0
    try:
        mem_query = f"whatsapp {chat_name}" if chat_name else "whatsapp recent"
        memories = await recall(mem_query)
        if memories and memories != "No relevant memories found.":
            result += f"\n\nFrom memory:\n{memories}"
    except Exception:
        pass  # mem0 failure shouldn't break the tool

    return result


# ── Reminders ────────────────────────────────────────────────────────────────


async def set_reminder(text: str, when: str, chat_id: int) -> str:
    def _set():
        # Validate date format before inserting
        dt = datetime.fromisoformat(when)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO reminders (text, due_at, chat_id) VALUES (?, ?, ?)",
                (text, dt.isoformat(), chat_id),
            )
            conn.commit()
        finally:
            conn.close()
        return f"Reminder set: \"{text}\" at {dt.strftime('%a %b %d %H:%M')}"

    return await asyncio.to_thread(_set)


async def check_due_reminders() -> list[tuple[int, int, str]]:
    """Read pending deliveries, including older reminders stored in local time."""

    def _check():
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT id, chat_id, text, due_at FROM reminders WHERE notified = 0"
            ).fetchall()
            due = []
            now = datetime.now(TZ)
            for row in rows:
                when = datetime.fromisoformat(row["due_at"])
                if when.tzinfo is None:
                    when = when.replace(tzinfo=TZ)
                if when.timestamp() <= now.timestamp():
                    due.append((row["id"], row["chat_id"], row["text"]))
            return due
        finally:
            conn.close()

    return await asyncio.to_thread(_check)


async def finish_reminder(reminder_id: int) -> None:
    def _finish():
        conn = get_db()
        try:
            conn.execute("UPDATE reminders SET notified = 1 WHERE id = ?", (reminder_id,))
            conn.commit()
        finally:
            conn.close()
    await asyncio.to_thread(_finish)


# ── Audio ──────────────────────────────────────────────────────────────────


async def transcribe_audio(file_path: str, timeout: int = TRANSCRIBE_TIMEOUT_SECONDS) -> str:
    """Transcribe audio file to text using faster-whisper."""
    from audio import transcriber
    return await asyncio.wait_for(
        asyncio.to_thread(transcriber.transcribe, file_path),
        timeout=timeout,
    )


async def generate_audio(text: str) -> dict:
    """Generate audio from text using Edge TTS. Returns {"audio_file": path}."""
    from audio import synthesizer
    audio_path = await synthesizer.speak_async(text)
    return {"audio_file": audio_path}
