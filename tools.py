"""Tool implementations for the MyGG personal AI assistant."""

import os
import json
import sqlite3
import asyncio
import threading
import logging
import re
from importlib.util import find_spec
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from utils import write_secret_file

from constants import (
    GOOGLE_PAGE_SIZE, GOOGLE_PAGE_LIMIT, TASK_NOTES_MAX_CHARS,
    EMAIL_BODY_MAX_CHARS, EMAIL_THREAD_MAX_MESSAGES, EMAIL_MIME_PART_LIMIT, GOOGLE_API_TIMEOUT_SECONDS,
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

TODO_BACKEND = os.getenv("TODO_BACKEND", "sqlite").lower().strip()
if TODO_BACKEND not in {"sqlite", "google"}:
    raise ValueError("TODO_BACKEND must be sqlite or google")

GOOGLE_ACCOUNTS = {
    "personal": {
        "token_path": Path(__file__).parent / "token.json",
        "label": os.getenv("GOOGLE_ACCOUNT_LABEL", "Personal Google account"),
        "email": os.getenv("GOOGLE_ACCOUNT_EMAIL", "").strip(),
    },
}
for account_name in os.getenv("GOOGLE_EXTRA_ACCOUNTS", "").split(","):
    account_name = account_name.strip()
    if not account_name or account_name in GOOGLE_ACCOUNTS:
        continue
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", account_name):
        raise ValueError("GOOGLE_EXTRA_ACCOUNTS needs lowercase names such as work,club.")
    GOOGLE_ACCOUNTS[account_name] = {
        "email": os.getenv(f"GOOGLE_ACCOUNT_{account_name.upper()}_EMAIL", "").strip(),
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
            CREATE TABLE IF NOT EXISTS conversation_history (
                chat_id INTEGER PRIMARY KEY, messages TEXT NOT NULL, updated_order INTEGER NOT NULL
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
        conn.execute("BEGIN IMMEDIATE")
        for table, columns in (("todos", {"notes": "TEXT", "source_id": "TEXT"}),
                               ("reminders", {"item_ref": "TEXT NOT NULL DEFAULT ''"})):
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_todo_source ON todos(source_id) WHERE source_id IS NOT NULL")
        conn.commit()
    finally:
        conn.close()
    from modules import feedback, goals, protagonism
    feedback.init_feedback()
    goals.init_goals()
    protagonism._init_table()



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
        memory = Memory.from_config(config)
        # mem0's Ollama adapters construct clients without a request deadline.
        import httpx
        for provider, seconds in ((memory.embedding_model, 10), (memory.llm, 300)):
            transport = getattr(provider.client, "_client", None)
            if not isinstance(transport, httpx.Client):
                raise RuntimeError("Cannot apply a safe timeout to this mem0 Ollama client.")
            transport.timeout = httpx.Timeout(seconds, connect=5, pool=5)
        chat = memory.llm.client.chat
        memory.llm.client.chat = lambda **kwargs: chat(**{"think": False, **kwargs})
        _memory_client = memory
    return _memory_client


def _memory_user_id() -> str:
    """Logical user-id mem0 partitions memories by. Defaults to USER_NAME (lowercased)."""
    return os.getenv("USER_NAME", "user").strip().lower() or "user"


async def remember(text: str, tags: list[str] | None = None, *, infer: bool = True, source: dict | None = None) -> str:
    if not memory_enabled():
        return "Long-term memory is turned off. See the memory setup in ONBOARDING.md."
    metadata = dict(source or {})
    if tags:
        metadata["tags"] = ",".join(tags)
    metadata["stored_at"] = datetime.now(TZ).isoformat()

    def _add():
        m = _get_memory()
        def store(infer, meta):
            result = m.add(text, user_id=_memory_user_id(), metadata=meta, infer=infer)
            rows = result.get("results", []) if isinstance(result, dict) else result
            return any(isinstance(row, dict) and row.get("id") and row.get("event") in {"ADD", "UPDATE"}
                       for row in (rows or []))
        if store(infer, metadata):
            return
        if infer and store(False, {**metadata, "verbatim_fallback": True}):
            return
        raise RuntimeError("The memory store did not confirm saving the observation.")

    await asyncio.to_thread(_add)
    return f"Stored: {text}"


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

    def _dated(r: dict) -> str:
        meta = r.get("metadata") or {}
        when = (meta.get("stored_at") or r.get("created_at") or "")[:10]
        source = [f"recorded {when or 'unknown'}"]
        if meta.get("channel") and meta.get("conversation_id"):
            source.extend([str(meta["channel"]), f"chat {meta['conversation_id']}"])
        else:
            source.append("source unknown")
        if meta.get("source_start"):
            source.append(f"observed {meta['source_start']} to {meta.get('source_end', meta['source_start'])}")
        if meta.get("tags"):
            source.append(f"tags {meta['tags']}")
        refs = meta.get("source_refs") or []
        if isinstance(refs, list):
            source.extend(str(ref) for ref in refs[:5])
            if len(refs) > 5:
                source.append(f"{len(refs) - 5} more source messages")
        return f"- [{'; '.join(source)}] {r['memory']}"

    return "Historical observations; verify current status and recipient before acting.\n" + "\n".join(_dated(r) for r in hits)


# ── Google Services (multi-account) ─────────────────────────────────────────

_google_refresh_lock = threading.Lock()


def _get_google_creds(account: str):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    acct = GOOGLE_ACCOUNTS.get(account)
    if not acct or not acct["token_path"].exists():
        return None
    with _google_refresh_lock:
        creds = Credentials.from_authorized_user_file(str(acct["token_path"]))
        if creds.expired and creds.refresh_token:
            transport = Request()
            def request(*args, **kwargs):
                kwargs.setdefault("timeout", 15)
                return transport(*args, **kwargs)
            creds.refresh(request)
            write_secret_file(acct["token_path"], creds.to_json())
        return creds


def _google_service(account, api, version):
    # googleapiclient uses httplib2, whose connections cannot be shared by research threads.
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    creds = _get_google_creds(account)
    if not creds:
        return None
    transport = AuthorizedHttp(creds, http=httplib2.Http(timeout=GOOGLE_API_TIMEOUT_SECONDS))
    return build(api, version, http=transport, cache_discovery=False)


def _get_calendar_service(account: str = "personal"):
    return _google_service(account, "calendar", "v3")


def _get_gmail_service(account: str = "personal"):
    return _google_service(account, "gmail", "v1")


def _available_accounts() -> list[str]:
    """Return account names that have token files."""
    return [name for name, cfg in GOOGLE_ACCOUNTS.items() if cfg["token_path"].exists()]


# ── Google Calendar ──────────────────────────────────────────────────────────


def _google_unavailable(account: str) -> str:
    assert isinstance(account, str), "account must be text"
    return f"Google account '{account}' unavailable; results are incomplete. Reauthorize with: python setup_calendar.py {account}"


def _list_google_pages(fetch, params: dict, item_key: str = "items", limit: int = 0) -> list[dict]:
    """Bounded pagination; fail explicitly instead of silently hiding later items."""
    assert callable(fetch) and isinstance(params, dict)
    params = dict(params)
    items = []
    for _ in range(GOOGLE_PAGE_LIMIT):
        result = fetch(**params).execute()
        items.extend(result.get(item_key, []))
        if limit and len(items) >= limit:
            return items[:limit]
        token = result.get("nextPageToken")
        if not token:
            return items
        params["pageToken"] = token
    raise RuntimeError(f"Google results exceed {GOOGLE_PAGE_LIMIT} pages; narrow the query.")


# ── Google Calendar ──────────────────────────────────────────────────────────


def _event_start(event):
    start = event["start"]
    dt = datetime.fromisoformat(start.get("dateTime") or start["date"])
    return dt.astimezone(TZ) if dt.tzinfo else dt.replace(tzinfo=TZ)


def _format_calendar_events(events: list, num_accounts: int) -> list[str]:
    """Format calendar events into human-readable lines."""
    from modules import feedback
    lines = []
    current_date = None
    for event in events:
        raw = event["start"].get("dateTime", event["start"].get("date", ""))
        summary = event.get("summary", "(no title)")
        ref = f"calendar:{event['_account']}:{event['id']}" if event.get("id") else ""
        if ref:
            feedback.register_item(ref, summary)
            if feedback.should_suppress(ref, summary):
                continue
        acct_tag = f" [{event['_account']}]" if num_accounts > 1 else ""
        if ref:
            acct_tag += f" [ref: {ref}]"
        dt = _event_start(event)
        day_str = dt.strftime("%a %b %d")
        if day_str != current_date:
            current_date = day_str
            lines.append(f"\n{day_str}:")
        when = dt.strftime("%H:%M") if "T" in raw else "All day"
        lines.append(f"  {when} - {summary}{acct_tag}")
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
                all_events.append({"_error": _google_unavailable(acct)})
                continue
            label = GOOGLE_ACCOUNTS[acct]["label"]
            try:
                events = _list_google_pages(service.events().list, {
                    "calendarId": "primary", "timeMin": start.isoformat(),
                    "timeMax": end.isoformat(), "singleEvents": True,
                    "orderBy": "startTime", "maxResults": GOOGLE_PAGE_SIZE,
                })
                for event in events:
                    event["_account"] = acct
                    event["_label"] = label
                    all_events.append(event)
            except Exception as e:
                all_events.append({"_error": f"{label}: {e}"})

        if not all_events:
            return f"No events for {start.strftime('%a %b %d')}." + (
                f" ({days} days)" if days > 1 else ""
            )

        errors = [e for e in all_events if "_error" in e]
        events = sorted((e for e in all_events if "_error" not in e), key=_event_start)

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
                events = _list_google_pages(service.events().list, {
                    "calendarId": "primary", "timeMin": now.isoformat(), "timeMax": end.isoformat(),
                    "singleEvents": True, "orderBy": "startTime", "maxResults": GOOGLE_PAGE_SIZE,
                })
                for event in events:
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
    from modules._web import _html_to_text

    assert isinstance(payload, dict), "email payload must be an object"
    pending, html = [payload], []
    for _ in range(EMAIL_MIME_PART_LIMIT):
        if not pending:
            break
        part = pending.pop(0)
        pending.extend(part.get("parts", [])[:EMAIL_MIME_PART_LIMIT - len(pending)])
        if part.get("filename"):
            continue
        encoded = part.get("body", {}).get("data")
        if not encoded or part.get("mimeType") not in ("text/plain", "text/html"):
            continue
        text = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8", errors="replace")
        if part["mimeType"] == "text/plain":
            return text
        html.append(_html_to_text(text))
    return "\n".join(html) or "(no text body)"


async def list_emails(query: str = "is:inbox", max_results: int = 10, account: str | None = None) -> str:
    assert isinstance(query, str) and query.strip(), "email query is required"
    if not isinstance(max_results, int) or not 1 <= max_results <= GOOGLE_PAGE_SIZE * GOOGLE_PAGE_LIMIT:
        raise ValueError("max_results is outside the supported range")
    def _list():
        from modules import feedback
        accounts = [account] if account else _available_accounts()
        if not accounts:
            return "No email accounts configured. Run: python setup_calendar.py"

        all_lines = []
        for acct in accounts:
            service = _get_gmail_service(acct)
            if not service:
                all_lines.append(f"⚠ {_google_unavailable(acct)}")
                continue
            label = GOOGLE_ACCOUNTS[acct]["label"]

            try:
                messages = _list_google_pages(service.users().messages().list, {
                    "userId": "me", "q": query, "maxResults": min(max_results, GOOGLE_PAGE_SIZE),
                }, "messages", limit=max_results)
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
                    source = f"gmail:{acct}:{msg.get('threadId', msg_ref.get('threadId', msg_ref['id']))}"
                    feedback.register_item(source, headers.get("subject", "(no subject)"))
                    if feedback.should_suppress(source, headers.get("subject", "")):
                        continue
                    snippet = msg.get("snippet", "")[:100]
                    unread = "UNREAD" in msg.get("labelIds", [])
                    marker = "[NEW] " if unread else ""
                    acct_tag = f" [{acct}]" if len(accounts) > 1 else ""
                    all_lines.append(
                        f"  {marker}{acct}:{msg_ref['id']}\n"
                        f"    Source-ID: {source}\n"
                        f"    From: {headers.get('from', '?')}\n"
                        f"    Subject: {headers.get('subject', '(no subject)')}\n"
                        f"    Date: {headers.get('date', '?')}\n"
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
        raise ValueError("Unknown Google account in the message ID.")
    return "personal", email_id


async def read_email(email_id: str) -> str:
    assert isinstance(email_id, str) and email_id.strip(), "email_id is required"
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
        thread_id = msg.get("threadId")
        if not thread_id:
            raise RuntimeError("Gmail did not return a thread ID; reply status is unknown.")
        thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
        messages = sorted(thread.get("messages", []), key=lambda m: int(m.get("internalDate", 0)))
        if not messages:
            raise RuntimeError("Gmail returned an empty thread; reply status is unknown.")
        delivered = [m for m in messages if "DRAFT" not in m.get("labelIds", [])]
        replied = bool(delivered and "SENT" in delivered[-1].get("labelIds", []))
        state = ("no delivered messages; this thread contains only drafts" if not delivered else
                 "latest message is from the user; do not create a reply task" if replied else
                 "latest delivered message is incoming; assess whether a reply is needed")
        lines = [f"Account: {acct}", f"Source-ID: gmail:{acct}:{thread_id}", f"Reply state: {state}"]
        if len(messages) > EMAIL_THREAD_MAX_MESSAGES:
            lines.append(f"Showing latest {EMAIL_THREAD_MAX_MESSAGES} of {len(messages)} thread messages.")
        # Newest first: the agent's total tool-result budget must retain the current request.
        for item in reversed(messages[-EMAIL_THREAD_MAX_MESSAGES:]):
            headers = _parse_email_headers(item.get("payload", {}).get("headers", []))
            body = _get_email_body(item.get("payload", {}))
            if len(body) > EMAIL_BODY_MAX_CHARS:
                body = body[:EMAIL_BODY_MAX_CHARS] + "\n... (truncated)"
            labels = item.get("labelIds", [])
            kind = "Unsent draft" if "DRAFT" in labels else "You" if "SENT" in labels else "Incoming"
            lines.append(f"\nMessage: {acct}:{item['id']} | {kind}\n"
                         f"From: {headers.get('from', '?')}\nTo: {headers.get('to', '?')}\n"
                         f"Date: {headers.get('date', '?')}\nSubject: {headers.get('subject', '(no subject)')}\n{body}")
        return "\n".join(lines)

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


async def add_todo(task: str, priority: str = "medium", due_date: str | None = None,
                   project: str | None = None, notes: str | None = None, source_id: str | None = None) -> str:
    from modules import feedback
    if not isinstance(task, str) or not task.strip() or priority not in {"high", "medium", "low"}:
        raise ValueError("A title and valid priority are required")
    if due_date:
        datetime.strptime(due_date, "%Y-%m-%d")
    if source_id:
        if len(source_id) > 256 or any(ch in source_id for ch in "\r\n"):
            raise ValueError("Invalid source reference")
        if feedback.is_suppressed(source_id, task):
            return "This item is dismissed or snoozed."
    if TODO_BACKEND == "google":
        from modules import _google_tasks
        return await _google_tasks.add_todo(task, priority, due_date, project, notes, source_id)
    def add():
        conn = get_db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id,status FROM todos WHERE source_id=?", (source_id,)).fetchone() if source_id else None
            if row:
                if row["status"] == "done":
                    return f"Todo already completed: #{row['id']}"
                conn.execute("UPDATE todos SET task=?,priority=?,due_date=COALESCE(?,due_date),notes=COALESCE(?,notes) WHERE id=?",
                             (task, priority, due_date, notes, row["id"]))
                ident, verb = row["id"], "updated"
            else:
                ident = conn.execute("INSERT INTO todos(task,priority,due_date,project,notes,source_id) VALUES(?,?,?,?,?,?)",
                                     (task,priority,due_date,project,notes,source_id or None)).lastrowid
                verb = "added"
            conn.commit()
            return f"Todo {verb}: #{ident} {task}"
        finally:
            conn.close()
    return await asyncio.to_thread(add)


async def list_todos(
    project: str | None = None,
    status: str = "pending",
    priority: str | None = None,
) -> str:
    if TODO_BACKEND == "google":
        from modules import _google_tasks
        return await _google_tasks.list_todos(project, status, priority)
    from modules import feedback
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
            ref = feedback.register_item(f"task:local:{r['id']}", r["task"], r["source_id"] or "")
            if feedback.should_suppress(ref, r["task"]):
                continue
            icon = "done" if r["status"] == "done" else "o"
            line = f"  [{icon}] #{r['id']} [{r['priority']}] {r['task']}"
            if r["project"]:
                line += f" @{r['project']}"
            if r["due_date"]:
                line += f" (due: {r['due_date']})"
            if r["notes"]:
                line += f" — {r['notes'][:TASK_NOTES_MAX_CHARS]}"
            lines.append(f"{line} [ref: {ref}]")
        return "\n".join(lines) or "No todos found."

    return await asyncio.to_thread(_list)


async def complete_todo(todo_id: int) -> str:
    if TODO_BACKEND == "google":
        from modules import _google_tasks
        return await _google_tasks.complete_todo(str(todo_id))
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
    if TODO_BACKEND == "google":
        from modules import _google_tasks
        return await _google_tasks.delete_todo(str(todo_id))
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


def _whatsapp_source_ref(message) -> str:
    """Prefer the provider ID; legacy rows keep their non-reused SQLite identity."""
    from urllib.parse import quote
    from modules import feedback
    assert message["id"] is not None
    provider_id = message["whatsapp_message_id"]
    suffix = quote(str(provider_id), safe="") if provider_id else f"local:{message['id']}"
    ref = f"whatsapp:{suffix}"
    feedback.register_item(ref, message["message_text"] or "")
    return ref


async def search_whatsapp(query: str, chat_name: str | None = None, hours_back: int = 24) -> str:
    """Search raw WhatsApp messages by text content."""
    def _search():
        conn = get_db()
        try:
            now = datetime.now(timezone.utc)
            cutoff = (now - timedelta(hours=hours_back)).isoformat().replace("+00:00", "Z")

            sql = "SELECT * FROM whatsapp_messages WHERE timestamp >= ? AND message_text IS NOT NULL"
            params: list = [cutoff]

            if query:
                sql += " AND message_text LIKE ?"
                params.append(f"%{query}%")
            if chat_name:
                sql += " AND (chat_jid = ? OR chat_name LIKE ?)"
                params.extend([chat_name, f"%{chat_name}%"])

            sql += " ORDER BY timestamp DESC LIMIT 50"
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        if not rows:
            return f"No WhatsApp messages matching '{query}' in the last {hours_back}h."

        from modules import feedback
        lines = []
        for r in rows:
            ref = _whatsapp_source_ref(r)
            if feedback.should_suppress(ref, r["message_text"]):
                continue
            sender = r["sender_name"] or r["sender_jid"]
            chat = r["chat_name"] or r["chat_jid"]
            ts = r["timestamp"][:16].replace("T", " ")
            direction = "You" if r["is_from_me"] else sender
            lines.append(f"[{ts}] {chat} [chat: {r['chat_jid']}] — {direction}: {r['message_text']} [ref: {ref}]")
        return "\n".join(lines) or "No WhatsApp messages to surface."

    return await asyncio.to_thread(_search)


async def get_whatsapp_summary(chat_name: str | None = None, hours_back: int = 24) -> str:
    """Get a summary of recent WhatsApp activity."""
    def _get():
        conn = get_db()
        try:
            now = datetime.now(timezone.utc)
            cutoff = (now - timedelta(hours=hours_back)).isoformat().replace("+00:00", "Z")

            if chat_name:
                sql = """SELECT id, whatsapp_message_id, chat_name, chat_jid, sender_name, sender_jid, message_text, is_from_me, timestamp
                         FROM whatsapp_messages
                         WHERE timestamp >= ? AND (chat_jid = ? OR chat_name LIKE ?) AND message_text IS NOT NULL
                         ORDER BY timestamp DESC LIMIT 100"""
                rows = conn.execute(sql, (cutoff, chat_name, f"%{chat_name}%")).fetchall()
            else:
                sql = """SELECT id, whatsapp_message_id, chat_name, chat_jid, sender_name, sender_jid, message_text, is_from_me, timestamp
                         FROM whatsapp_messages
                         WHERE timestamp >= ? AND message_text IS NOT NULL
                         ORDER BY timestamp DESC LIMIT 100"""
                rows = conn.execute(sql, (cutoff,)).fetchall()
        finally:
            conn.close()

        if not rows:
            return f"No WhatsApp messages in the last {hours_back}h."

        # Group by chat
        from modules import feedback
        chats: dict[str, list] = {}
        labels: dict[str, str] = {}
        for r in rows:
            ref = _whatsapp_source_ref(r)
            if feedback.should_suppress(ref, r["message_text"]):
                continue
            chat = r["chat_jid"]
            if chat not in chats:
                chats[chat] = []
                labels[chat] = r["chat_name"] or chat
            sender = "You" if r["is_from_me"] else (r["sender_name"] or r["sender_jid"])
            chats[chat].append(f"[{r['timestamp']}] {sender}: {r['message_text']} [ref: {ref}]")

        lines = ["Recent WhatsApp sample (up to 100 messages total; latest five per chat, oldest first):"] if chats else []
        for chat, msgs in chats.items():
            lines.append(f"\n{labels[chat]} [chat: {chat}] ({len(msgs)} messages in sample):")
            # Show last 5 messages per chat in summary
            for m in reversed(msgs[:5]):
                lines.append(f"  {m}")
            if len(msgs) > 5:
                lines.append(f"  ... and {len(msgs) - 5} more")

        return "\n".join(lines) or "No WhatsApp messages to surface."

    # Historical memory is available through recall, not mixed into a live message read.
    return await asyncio.to_thread(_get)


def _resolve_wa_jid(query: str) -> tuple[str | None, str]:
    """Freeze an exact contact or group ID before the owner reviews the draft."""
    import re
    if not isinstance(query, str) or not query.strip():
        return None, "Specify a chat name or WhatsApp ID."
    query = query.strip()
    valid_jid = r"(?:[0-9]+(?:-[0-9]+)?@g\.us|[0-9]+@(?:s\.whatsapp\.net|lid))"
    if re.fullmatch(valid_jid, query):
        return query, query
    conn = get_db()
    try:
        def matches(name):
            return conn.execute("""
                SELECT chat_jid, chat_name, MAX(timestamp) AS latest FROM whatsapp_messages
                WHERE instr(lower(chat_name), lower(?)) > 0 GROUP BY chat_jid
                ORDER BY latest DESC LIMIT 6""", (name,)).fetchall()
        rows = matches(query)
        if len(rows) == 1 and re.fullmatch(valid_jid, rows[0]["chat_jid"]):
            return rows[0]["chat_jid"], rows[0]["chat_name"]
        if not rows:
            suggestions = {r["chat_name"] for part in query.split() if len(part) >= 3 for r in matches(part)}
            hint = " Possible matches: " + ", ".join(sorted(suggestions)[:5]) if suggestions else ""
            return None, "No matching WhatsApp chat. Ask which chat to use." + hint
        choices = ", ".join(f"{r['chat_name']} ({r['chat_jid']})" for r in rows)
        return None, f"Ambiguous or unsupported WhatsApp chat. Ask which exact chat to use: {choices}"
    finally:
        conn.close()


def _wa_control():
    port = int(os.getenv("WHATSAPP_HEALTH_PORT") or 8786)
    secret = os.getenv("WHATSAPP_BRIDGE_SECRET", "").strip()
    return f"http://127.0.0.1:{port}", {"Authorization": f"Bearer {secret}"} if secret else {}


async def whatsapp_status() -> dict:
    import httpx
    url, headers = _wa_control()
    if not headers:
        return {"connection": "disabled"}
    try:
        async with httpx.AsyncClient(timeout=4, trust_env=False) as client:
            response = await client.get(f"{url}/health", headers=headers)
            response.raise_for_status()
            status = response.json()
            if status.get("connection") in {"open", "connecting", "close"}:
                return {"connection": status["connection"], "pending_incoming": int(status["pending_incoming"])}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return {"connection": "unavailable"}


async def _deliver_whatsapp(draft: dict) -> str:
    import httpx
    url, headers = _wa_control()
    if not headers:
        raise RuntimeError("WhatsApp bridge is unconfigured")
    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
        response = await client.post(f"{url}/send", headers=headers,
                                     json={"jid": draft["recipient"], "text": draft["text"]})
        response.raise_for_status()
        receipt = response.json().get("id")
        if not isinstance(receipt, str) or not receipt:
            raise RuntimeError("WhatsApp did not return a message ID")
    return f"Sent to {draft['label']} on WhatsApp (receipt {receipt})."


# ── Reminders ────────────────────────────────────────────────────────────────


async def set_reminder(text: str, when: str, chat_id: int, item_ref: str = "") -> str:
    def _set():
        # Validate date format before inserting
        dt = datetime.fromisoformat(when)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO reminders (text, due_at, chat_id, item_ref) VALUES (?, ?, ?, ?)",
                (text, dt.isoformat(), chat_id, item_ref),
            )
            conn.commit()
        finally:
            conn.close()
        return f"Reminder set: \"{text}\" at {dt.strftime('%a %b %d %H:%M')}"

    return await asyncio.to_thread(_set)


async def get_due_reminders() -> list[tuple[int, int, str, str]]:
    """Read pending deliveries, including older reminders stored in local time."""

    def _check():
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT id, chat_id, text, due_at, item_ref FROM reminders WHERE notified = 0"
            ).fetchall()
            due = []
            now = datetime.now(TZ)
            for row in rows:
                try:
                    when = datetime.fromisoformat(row["due_at"])
                except ValueError:
                    logger.warning("Invalid date for reminder %s", row["id"])
                    continue
                if when.tzinfo is None:
                    when = when.replace(tzinfo=TZ)
                if when.timestamp() <= now.timestamp():
                    due.append((row["id"], row["chat_id"], row["text"], row["item_ref"]))
            return due
        finally:
            conn.close()

    return await asyncio.to_thread(_check)


async def check_due_reminders() -> list[tuple[int, int, str]]:
    return [(ident, chat, text) for ident, chat, text, _ in await get_due_reminders()]


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


async def generate_audio(text: str, language: str | None = None) -> dict:
    """Use the configured speech engine and return its temporary audio file."""
    from audio import synthesizer
    audio_path = await synthesizer.speak_async(text, language)
    return {"audio_file": audio_path}
