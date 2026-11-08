"""Proactive follow-ups, portfolio review, knowledge search and weekly leisure suggestions."""
import os
import re
import random
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, time as dt_time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import llm
import tools
from modules import feedback

logger = logging.getLogger(__name__)

TZ = ZoneInfo(os.getenv("TIMEZONE", "America/New_York"))
OWNER_CHAT_ID = 0  # Set after the Telegram allowlist is validated.
BOARD_DIR = Path(os.environ["BOARD_DIR"]).expanduser() if os.getenv("BOARD_DIR") else None
WAKE_START, WAKE_END = dt_time(9, 0), dt_time(20, 30)
DAILY_BACKSTOP = 8  # ponytail: runaway guard, not a volume policy — free rein lives in the prompt
FOLLOWUP_PREFIX = "[FOLLOWUP] "

_last_user_activity: datetime | None = None
_scout_lock = asyncio.Lock()


# ── initiative_log (SQLite, pa.db) ────────────────────────────────────────────

def _init_table():
    try:
        conn = tools.get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS initiative_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    summary TEXT,
                    source TEXT NOT NULL,
                    fired INTEGER NOT NULL DEFAULT 0,
                    sent_at TEXT NOT NULL DEFAULT (datetime('now')))"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_init_topic ON initiative_log(topic, sent_at)"
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("initiative_log init failed")


def _log_decision(topic: str, summary: str, *, source: str, fired: int) -> None:
    # ponytail: sync sqlite on the event loop — <5ms typical (WAL, tiny table), 10s
    # ceiling if writer-locked; wrap call sites in to_thread if that ever bites.
    def _insert():
        conn = tools.get_db()
        try:
            conn.execute(
                "INSERT INTO initiative_log (topic, summary, source, fired, sent_at) VALUES (?, ?, ?, ?, ?)",
                (topic, (summary or "")[:500], source, fired, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

    try:
        _insert()
    except sqlite3.OperationalError:
        _init_table()  # table may have been lost to a locked-DB import; retry once
        _insert()


def _recent_decisions(days: int = 7, fired_only: bool = False):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    q = "SELECT topic, summary, source, fired, sent_at FROM initiative_log WHERE sent_at >= ?"
    if fired_only:
        q += " AND fired = 1"
    conn = tools.get_db()
    try:
        return conn.execute(q + " ORDER BY sent_at DESC", (cutoff,)).fetchall()
    finally:
        conn.close()


def _fired_count_today() -> int:
    local_midnight = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = local_midnight.astimezone(timezone.utc).isoformat()
    conn = tools.get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM initiative_log WHERE fired = 1 AND sent_at >= ?", (cutoff,)
        ).fetchone()
        return row[0]
    finally:
        conn.close()


# ── guards ────────────────────────────────────────────────────────────────────

def note_user_activity() -> None:
    """Called from main.py message handlers — feeds the mid-conversation guard."""
    global _last_user_activity
    _last_user_activity = datetime.now(timezone.utc)


def _user_active_within(minutes: int = 20) -> bool:
    if _last_user_activity is None:
        return False
    return datetime.now(timezone.utc) - _last_user_activity < timedelta(minutes=minutes)


def _in_waking_window(now: datetime) -> bool:
    return WAKE_START <= now.timetz().replace(tzinfo=None) < WAKE_END


# ── portfolio board ───────────────────────────────────────────────────────────

_FM_RE = re.compile(r"^---\n(.*?)\n---", re.S)
_AC_RE = re.compile(r"<!-- AC:BEGIN -->(.*?)<!-- AC:END -->", re.S)


def _read_board() -> str:
    if BOARD_DIR is None or not BOARD_DIR.is_dir():
        return "(No portfolio board is configured or accessible.)"
    lines = [
        "Portfolio board (declared plans; check dates for current status):"
    ]
    cards = []
    for f in sorted(BOARD_DIR.glob("task-*.md"))[:200]:
        if not f.resolve().is_relative_to(BOARD_DIR.resolve()) or f.stat().st_size > 500_000:
            continue
        try:
            text = f.read_text(encoding="utf-8")
            fm = _FM_RE.search(text)
            meta = {}
            if fm:
                for line in fm.group(1).splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip().strip("'\"")
            ac = _AC_RE.search(text)
            open_items = []
            if ac or text:
                open_items = [
                    m.strip() for m in re.findall(r"^- \[ \] (.+)$", ac.group(1) if ac else text, re.M)
                ][:3]
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
            cards.append(
                (meta.get("status", "?"), meta.get("priority", ""),
                 meta.get("title", f.stem), open_items, mtime, f.stem)
            )
        except Exception:
            logger.exception(f"board parse failed for {f.name}")
    order = {"Active": 0, "Building": 1, "Idea": 2, "Maintenance": 3, "Paused": 4}
    for status, prio, title, items, mtime, card_id in sorted(cards, key=lambda c: order.get(c[0], 9)):
        prio_s = f" ({prio})" if prio else ""
        lines.append(f"[{status}]{prio_s} {title} (updated {mtime})")
        for it in items:
            ref = f"board:{feedback.text_ref(card_id).partition(':')[2]}:{feedback.text_ref(it).partition(':')[2]}"
            feedback.register_item(ref, it)
            if not feedback.should_suppress(ref, it):
                lines.append(f"    next: {it} [ref:{ref}]")
    return "\n".join(lines)


def _search_local_notes(question: str, top_k: int, directory: Path) -> str:
    import heapq
    root = directory.expanduser().resolve()
    if not root.is_dir():
        return "Knowledge directory is unavailable."
    words = set(re.findall(r"\w{2,}", question.casefold()))
    results = []
    for path in root.rglob("*"):
        if path.suffix.lower() not in {".md", ".txt"} or not path.is_file():
            continue
        try:
            if not path.resolve().is_relative_to(root) or path.stat().st_size > 2_000_000:
                continue
            paragraphs = path.read_text(encoding="utf-8").split("\n\n")
        except (OSError, UnicodeError):
            continue
        for paragraph in paragraphs:
            score = len(words & set(re.findall(r"\w+", paragraph.casefold())))
            if score:
                hit = (score, str(path.relative_to(root)), paragraph.strip()[:700])
                if len(results) < top_k:
                    heapq.heappush(results, hit)
                elif hit > results[0]:
                    heapq.heapreplace(results, hit)
    results.sort(key=lambda row: -row[0])
    return "\n".join(f"- {snippet} ({name})" for _, name, snippet in results[:top_k]) or "No matching notes."


async def _search_knowledge_base(question: str, top_k: int = 5) -> str:
    if not isinstance(question, str) or not question.strip():
        return "Enter a search question."
    top_k = max(1, min(int(top_k), 10))
    if os.getenv("KNOWLEDGE_DIR"):
        return await asyncio.to_thread(_search_local_notes, question, top_k, Path(os.environ["KNOWLEDGE_DIR"]))
    url, collection = os.getenv("KNOWLEDGE_QDRANT_URL"), os.getenv("KNOWLEDGE_COLLECTION")
    if llm.uses_local(llm.tool_caller.get()) or not (url and collection and os.getenv("OPENAI_API_KEY")):
        return "Set KNOWLEDGE_DIR to search your own Markdown or text notes."
    def query():
        from openai import OpenAI
        from qdrant_client import QdrantClient
        with OpenAI(timeout=20, max_retries=0) as client:
            vector = client.embeddings.create(model=os.getenv("KNOWLEDGE_EMBED_MODEL", "text-embedding-3-large"),
                                              input=question).data[0].embedding
        db = QdrantClient(url=url, api_key=os.getenv("KNOWLEDGE_QDRANT_API_KEY"), timeout=20)
        try:
            hits = db.query_points(collection_name=collection, query=vector, limit=top_k).points
        finally:
            db.close()
        matches = []
        for hit in hits:
            item = hit.payload or {}
            if item.get("content"):
                matches.append(f"- {str(item['content'])[:700]} ({item.get('source') or item.get('title') or 'unknown source'})")
        return "\n".join(matches) or "No matching notes."
    return await asyncio.wait_for(asyncio.to_thread(query), timeout=45)


# ── schedule_followup (L2) ────────────────────────────────────────────────────

def _parse_followup(text: str) -> tuple[str, str]:
    body = text[len(FOLLOWUP_PREFIX):]
    topic, _, note = body.partition(" | ")
    return topic.strip(), note.strip()


async def _schedule_followup(when: str, topic: str, note: str, item_ref: str = "") -> str:
    if not OWNER_CHAT_ID:
        return "Set OWNER_CHAT_ID to enable scheduled follow-ups."
    ref = feedback.register_item("topic:" + topic, note, item_ref)
    if feedback.is_suppressed(ref, note):
        return "Follow-up not scheduled: the user dismissed or postponed this item."
    try:
        due = datetime.fromisoformat(when)
        if due.tzinfo is None:
            due = due.replace(tzinfo=TZ)
    except ValueError:
        return f"Could not parse '{when}' — use ISO format like YYYY-MM-DDTHH:MM"

    def _insert():
        conn = tools.get_db()
        try:
            conn.execute(
                "INSERT INTO reminders (text, due_at, chat_id, item_ref) VALUES (?, ?, ?, ?)",
                (f"{FOLLOWUP_PREFIX}{topic} | {note}", due.isoformat(), OWNER_CHAT_ID, ref),
            )
            conn.commit()
        finally:
            conn.close()

    await asyncio.to_thread(_insert)
    return f"Follow-up scheduled for {due.strftime('%A %Y-%m-%d %H:%M')}: {topic}"


# ── scout (stage 1) ───────────────────────────────────────────────────────────

SCOUT_TOOLS = ["recall", "list_todos", "get_calendar", "list_emails", "search_whatsapp",
               "get_whoop_recovery", "get_whoop_sleep"]

SCOUT_SYSTEM = (
    "You are the initiative scout for GG, the user's assistant. You review their live state and decide "
    "whether ONE thing is worth proactively raising with the user right now. You are NOT GG — output "
    "data, not a message to the user.\n"
    "Most runs end in NO_ACTION — then output exactly: NO_ACTION: <one-line reason>\n"
    "Raise something ONLY for: (a) time-sensitive risk to a commitment; (b) a promise quietly "
    "slipping — pick the ONE highest-leverage, not the longest-slipping; (c) a genuine finished "
    "thing to celebrate; (d) a cross-project pattern the user can't see; (e) a wellbeing signal that "
    "changes today's plan; (f) a money signal — an unexpected charge, a cost spike, a failed "
    "payment, an obvious leak. Money anomalies always clear the bar.\n"
    "OUTCOME LOOP: for topics you nudged 1-3 days ago (in the fired list), briefly verify whether "
    "they actually resolved — calendar, todos, or email evidence. Resolved → celebrate ONCE with "
    "slug <topic>-resolved. Clearly ignored AND still time-critical → the -escalation rule. "
    "Otherwise say nothing about them.\n"
    "THE BAR RISES STEEPLY with each nudge already sent today — after 2, only a same-day deadline "
    "or a finished win clears it. Never raise a topic substantially-same as an already-nudged one. "
    "When you mean the same issue as a listed topic, reuse its EXACT slug — never reword slugs. "
    "Explicit dismissal overrides escalation: do not reopen a rejected item. "
    "For an item the user has NOT dismissed, a hard deadline inside the next 24-48h may be re-raised ONCE, as escalation "
    "('this is tomorrow'), not repetition — append -escalation to the original slug "
    "(e.g. project-review → project-review-escalation). Celebrations: each win only once.\n"
    "Use a stable item reference actually returned by a tool or the board; never invent one. "
    "To raise, output exactly:\nTOPIC: <kebab-slug>\nITEM_REF: <stable source reference>"
    "\nWHY_NOW: <consequence and why an interruption is needed>\nEVIDENCE: <2-4 lines>"
)


def _scout_prompt(trigger: str | None = None) -> str:
    now = datetime.now(TZ)
    # Fired nudges shown unconditionally (≤8/day by backstop) — silent/briefing rows are
    # noise-capped so a busy flag day can't push real nudges out of the scout's view.
    fired = _recent_decisions(days=7, fired_only=True)
    fired_lines = "\n".join(
        f"- [{r['sent_at'][:10]}] {r['topic']}: {r['summary']}" for r in fired
    ) or "(none)"
    silent = [r for r in _recent_decisions(days=7) if not r["fired"]][:10]
    silent_lines = "\n".join(
        f"- [{r['source']} {r['sent_at'][:10]}] {r['topic']}: {r['summary']}" for r in silent
    ) or "(none)"
    parts = [
        f"Now: {now.strftime('%A %Y-%m-%d %H:%M')} ({TZ})",
        f"Nudges already sent today: {_fired_count_today()}",
        f"Topics you already NUDGED — never re-raise (except the -escalation rule):\n{fired_lines}",
        f"Recent silent passes and briefing-surfaced topics (context):\n{silent_lines}",
        f"Portfolio board:\n{_read_board()}",
        feedback.build_feedback_context(),
    ]
    if trigger:
        parts.append(f"WAKE TRIGGER (why this scout ran now — weigh it first): {trigger}")
    parts.append("Check todos, calendar, memories and Whoop as needed, then decide.")
    return "\n\n".join(parts)


def _parse_scout(text: str) -> tuple[str, str] | None:
    if not text:
        return None
    if text.strip().upper().startswith("NO_ACTION"):  # anchored: a real nudge may mention the token
        return None
    m = re.search(r"^TOPIC:\s*(\S+)", text, re.M)
    if not m:
        return None
    return (m.group(1).strip().lower(), text.strip())


def _is_dup(topic: str) -> bool:
    if feedback.is_suppressed("topic:" + topic):
        return True
    return any(r["topic"] == topic for r in _recent_decisions(days=7, fired_only=True))


def extract_flag(text: str) -> str | None:
    """Parse an INITIATIVE_FLAG line from an event job's output (L1)."""
    if not text:
        return None
    m = re.search(r"^INITIATIVE_FLAG:\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else None


def strip_flag(text: str) -> str:
    """Remove the INITIATIVE_FLAG line so it never leaks into user-facing summaries."""
    if not text:
        return text
    return re.sub(r"^INITIATIVE_FLAG:.*$", "", text, flags=re.M).strip()


# ── compose (stage 2) ─────────────────────────────────────────────────────────

_SENTINELS = (
    "I'm still working on something",
    "Sorry, I hit an API error",
    "Sorry, the API is overloaded",
    "I got stuck in a loop",
    "Sorry, my local brain hit an error",   # agent.py local-branch failure
    "I couldn't produce a usable answer",
    "(no response)",                        # a thinking-only turn: no text block at all
)


def _is_failure_sentinel(text: str) -> bool:
    result = (text or "").strip()
    return (any(result.startswith(s) for s in _SENTINELS) or
            result.endswith("This response was cut short before I could finish."))


async def _compose_and_send(context, prompt_body: str, topic: str, source: str) -> bool:
    """Compose via full persona and deliver (text + any celebration voice notes)."""
    import agent
    import heartbeat

    source_match = re.search(r"^ITEM_REF:\s*(\S+)", prompt_body, re.M)
    ref = source_match.group(1) if source_match else "topic:" + topic
    if feedback.is_suppressed(ref):
        return True
    feedback.register_item("topic:" + topic, "", ref)
    read_tools = SCOUT_TOOLS + ["read_email", "get_portfolio_board", "search_knowledge_base",
                               "web_search", "web_fetch", "send_audio"]
    if source == "leisure":
        read_tools += ["schedule_followup", "remember"]
    with feedback.proactive_scope():
        response, audio_files = await agent.handle_message(OWNER_CHAT_ID, prompt_body, allowed_tools=read_tools)
    try:
        if _is_failure_sentinel(response):
            logger.warning(f"initiative compose hit failure sentinel for '{topic}' ({source})")
            _log_decision(topic, "compose-failed", source=source, fired=0)
            return False
        if feedback.is_suppressed(ref):
            return True
        await heartbeat._send_heartbeat(context, response, notice_ref=ref)
        for path in audio_files:
            try:
                with open(path, "rb") as f:
                    await context.bot.send_voice(chat_id=OWNER_CHAT_ID, voice=f)
            except Exception:
                logger.exception("initiative voice send failed")
        try:
            _log_decision(topic, response, source=source, fired=1)
        except Exception:
            logger.exception("Could not record an already delivered initiative")
        return True
    finally:
        # Unlink even on sentinel return or a heartbeat send failure
        for path in audio_files:
            try:
                os.unlink(path)
            except OSError:
                pass


# ── pipeline ──────────────────────────────────────────────────────────────────

async def scout_now(context, trigger: str | None = None):
    """One scout pass; compose+send only if the bar is cleared. Never raises."""
    if _scout_lock.locked():
        return
    async with _scout_lock:
        await _scout(context, trigger)


async def _scout(context, trigger):
    try:
        if not OWNER_CHAT_ID:
            return
        if not _in_waking_window(datetime.now(TZ)):
            return
        if _user_active_within(20):
            logger.info("initiative: deferred — the user active in the last 20m")
            return
        if _fired_count_today() >= DAILY_BACKSTOP:
            logger.warning("initiative: daily backstop reached, skipping")
            return
        import agent

        with feedback.proactive_scope():
            raw = await agent.run_subagent(
                system=SCOUT_SYSTEM,
                prompt=await asyncio.to_thread(_scout_prompt, trigger),
                allowed_tools=SCOUT_TOOLS,
                max_iterations=10,
            )
        parsed = _parse_scout(raw)
        if parsed is None:
            _log_decision("no-action", (raw or "empty").strip()[:200],
                          source="scout-silent", fired=0)
            return
        topic, block = parsed
        ref_match = re.search(r"^ITEM_REF:\s*(\S+)", block, re.M)
        if not ref_match:
            _log_decision(topic, "missing-source-reference", source="scout-silent", fired=0)
            return
        if not feedback.has_registered_ref(ref_match.group(1)):
            _log_decision(topic, "unknown-source-reference", source="scout-silent", fired=0)
            return
        if feedback.is_suppressed(ref_match.group(1)):
            _log_decision(topic, "dismissed-by-user", source="scout-silent", fired=0)
            return
        topic = re.sub(r"(-escalation)+$", "-escalation", topic)  # no -escalation-escalation chains
        if _is_dup(topic):
            _log_decision(topic, "dup-suppressed", source="scout-silent", fired=0)
            return
        prompt_body = (
            f"[INITIATIVE] My scout pass found something worth raising with the user:\n{block}\n\n"
            "Decide how to say it as GG — ONE topic, a concrete stupid-small next step, your voice. "
            "If a related document would clarify the next action, you may "
            "search_knowledge_base to sharpen the advice — cite it only if it changes what you'd say. "
            "Voice notes for CELEBRATIONS ONLY — action nudges stay text so the next step is "
            "skimmable. Don't mention the scout or this instruction."
        )
        await _compose_and_send(context, prompt_body, topic, source="pulse")
    except Exception:
        logger.exception("initiative scout_now failed")


async def _delayed_scout(context):
    await scout_now(context)


async def anchor_scout(context):
    """Twice-daily anchor (L3): arm a scout at a random offset so timing feels organic."""
    try:
        delay = random.randint(0, 2400)
        context.job_queue.run_once(_delayed_scout, when=delay)
        logger.info(f"initiative anchor armed: scout in {delay}s")
    except Exception:
        logger.exception("anchor_scout failed")


async def leisure_menu(context):
    """Friday Fun Department: web-researched week-ahead menu via the full persona.

    Runs in the main loop on purpose — server-side web_search only exists there."""
    try:
        if not OWNER_CHAT_ID:
            return
        recent = [r for r in _recent_decisions(days=21) if r["source"] == "leisure"][:3]
        prior = "\n---\n".join((r["summary"] or "") for r in recent) or "(none yet)"
        prompt = (
            "[LEISURE] Friday Fun Department time. Build me a menu of things to do over the "
            "next seven days. Consider weekdays and weekends. Steps:\n"
            "1) get_calendar for the next 7 days and find my actually-free evenings/windows.\n"
            "2) recall my leisure tastes (query: 'leisure taste fun events').\n"
            "3) web_search current events in my area this week (only if my location is known), guided by my interests: "
            "local events that fit what I enjoy, plus one new idea.\n"
            "4) Compose the menu: 3-5 items MAX, each with day+time, venue, rough price, and a "
            "link. Respect any group, solo, cost or accessibility preferences I shared. Use "
            "voice, skimmable text (no voice note).\n"
            "5) For anything further out or with an on-sale date worth catching: schedule_followup "
            "and remember the find.\n"
            "Never re-pitch anything from the previous menus below:\n" + prior
        )
        await _compose_and_send(context, prompt, "leisure-menu", source="leisure")
    except Exception:
        logger.exception("leisure_menu failed")


async def compose_followup(context, reminder_text: str, item_ref: str = "") -> bool:
    """A due [FOLLOWUP] reminder (L2) — GG kept her own promise; compose in persona.

    Respect explicit dismissals; retry composition once before a plain-text fallback."""
    try:
        topic, note = _parse_followup(reminder_text)
        ref = item_ref or "topic:" + topic
        if feedback.is_suppressed(ref, note):
            return True
        is_retry = note.endswith(" (retrying)")
        note = note.removesuffix(" (retrying)")
        note = note or "(no note saved — reconstruct the context from the topic and your tools before speaking)"
        prompt_body = (
            f"[INITIATIVE] A follow-up YOU scheduled is due now — topic '{topic}': {note}\n"
            f"ITEM_REF: {ref}\n"
            "You promised the user you'd check in on this. Do it as GG: check current state with your "
            "tools if useful (calendar, todos, memories), then deliver the check-in — warm, "
            "specific, one stupid-small next step if action is needed. Voice notes for "
            "CELEBRATIONS ONLY — if this check-in asks the user to act, stay in text. Don't mention "
            "schedulers or reminder infrastructure; this is you keeping your word."
        )
        sent = await _compose_and_send(context, prompt_body, f"followup-{topic}", source="followup")
        if not sent:
            if not is_retry:
                retry_at = (datetime.now(TZ) + timedelta(minutes=30)).isoformat()
                await _schedule_followup(retry_at, topic, f"{note} (retrying)", ref)
                logger.info(f"followup '{topic}' re-armed for +30min after compose failure")
            else:
                # Second failure — degrade to a plain bell so the promise still surfaces
                sent_message = await context.bot.send_message(
                    chat_id=OWNER_CHAT_ID, text=f"\U0001f514 Follow-up: {topic} — {note}"
                )
                try:
                    feedback.record_notice(OWNER_CHAT_ID, sent_message.message_id, ref, note)
                    _log_decision(f"followup-{topic}", "delivered-as-bell", source="followup", fired=1)
                except Exception:
                    logger.exception("Could not record an already delivered follow-up")
        return True
    except Exception:
        logger.exception("compose_followup failed")
        return False


# ── module contract ───────────────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "get_portfolio_board",
        "description": (
            "Read the user's configured portfolio board: every project's lifecycle stage, "
            "priority, and declared next actions. Treat it as declared intent, not live state."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_knowledge_base",
        "description": (
            "Search the user-configured knowledge base and return relevant passages with their sources."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Question to search for"},
                "top_k": {"type": "integer", "description": "Results to return, default 5"},
            },
            "required": ["question"],
        },
    },
    {
        "name": "schedule_followup",
        "description": (
            "Schedule YOUR OWN future check-in with the user. Use when a deadline or commitment "
            "surfaces and you want to proactively follow up at the right moment (e.g. the "
            "afternoon before an event). You will be woken at that time to compose the check-in. "
            "This is how you keep promises like 'I'll check on you Thursday'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "when": {"type": "string", "description": "Local ISO datetime, e.g. YYYY-MM-DDTHH:MM"},
                "topic": {"type": "string", "description": "Short kebab-case slug for the follow-up"},
                "note": {"type": "string", "description": "What to check and why — context for future-you"},
                "item_ref": {"type": "string", "description": "Stable source reference from tools; preserves the user's feedback across renamed follow-ups"},
            },
            "required": ["when", "topic"],
        },
    },
]


async def execute_tool(name: str, input_data: dict) -> str | None:
    try:
        if name == "get_portfolio_board":
            return await asyncio.to_thread(_read_board)
        if name == "search_knowledge_base":
            return await _search_knowledge_base(
                input_data["question"], int(input_data.get("top_k", 5))
            )
        if name == "schedule_followup":
            return await _schedule_followup(
                input_data["when"], input_data["topic"], input_data.get("note", ""), input_data.get("item_ref", "")
            )
        return None
    except Exception as e:
        logger.exception(f"protagonism tool {name} failed")
        return f"Tool error ({name}): {e}"


async def heartbeat_contributions() -> str | None:
    """Board one-liner for briefings; logs surfaced topics so the scout won't re-deliver them.

    Mondays append last week's fire rate — free-rein-compatible transparency."""

    def _build():
        if BOARD_DIR is None or not BOARD_DIR.is_dir():
            return None
        board = _read_board()
        actions = [
            l.strip()[6:] for l in board.splitlines() if l.strip().startswith("next: ")
        ]
        filtered = []
        for action in actions:
            match = re.search(r" \[ref:([^\]]+)\]$", action)
            title = action[:match.start()] if match else action
            ref = match.group(1) if match else feedback.text_ref(title)
            if not feedback.is_suppressed(ref, title):
                filtered.append(action)
        actions = filtered[:3]
        if not actions:
            return None
        recent_briefing = {r["topic"] for r in _recent_decisions(days=1) if r["source"] == "briefing"}
        for a in actions:
            slug = re.sub(r"[^a-z0-9]+", "-", a.lower())[:40].strip("-")
            if slug not in recent_briefing:  # don't crowd the scout's context window
                _log_decision(slug, a, source="briefing", fired=0)
        line = "top next-actions: " + " · ".join(actions)
        if datetime.now(TZ).weekday() == 0:
            week = _recent_decisions(days=7, fired_only=True)
            line += f" (I nudged {len(week)}× last week)"
        return line

    return await asyncio.to_thread(_build)
