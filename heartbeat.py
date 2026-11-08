"""Proactive heartbeat system — morning briefings, meeting alerts, evening wrap-ups.

Morning and evening briefings use the parallel sub-agent pattern:
specialised researchers run in parallel (asyncio.gather), each with a tight
toolset and isolated context. Their synthesised outputs are then handed to the
main agent for a single composition pass. This cuts token usage and avoids
"30 tools available" prompt bloat in the main loop.

Module heartbeat contributions are collected alongside research outputs so
custom modules (whoop, weather, ...) can inject one-line
signals without going through a sub-agent.
"""

import asyncio
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import agent
import tools
import modules
from modules import protagonism, feedback
from utils import split_long_message
from constants import PRE_MEETING_ALERT_WINDOW_MIN, MEETING_LOOKAHEAD_MIN

logger = logging.getLogger(__name__)

TZ = ZoneInfo(os.getenv("TIMEZONE", "America/New_York"))
OWNER_CHAT_ID = 0  # Set after the Telegram allowlist is validated.

# Pre-meeting alert state
_alerted_events: set[str] = set()
_last_reset_date: str = ""


# ── messaging ─────────────────────────────────────────────────────────────────

async def _send_heartbeat(context, text: str, notice_ref: str = ""):
    """Send a heartbeat message to the owner, splitting if needed."""
    assert isinstance(text, str), "text must be string"
    if not OWNER_CHAT_ID:
        logger.warning("OWNER_CHAT_ID not set, skipping heartbeat")
        return

    for chunk in split_long_message(text):
        sent = await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=chunk)
        try:
            from modules import feedback
            feedback.record_notice(OWNER_CHAT_ID, sent.message_id, notice_ref, chunk)
        except Exception:
            logger.exception("Could not bind delivered notification to its source")


def _feedback_context() -> str:
    return f"\nNow: {datetime.now(TZ).isoformat()}\n{feedback.build_feedback_context()}"


async def _run_proactive_subagent(**kwargs):
    assert "system" in kwargs and "allowed_tools" in kwargs
    with feedback.proactive_scope():
        return await agent.run_subagent(**kwargs)


# ── sub-agent research tasks ──────────────────────────────────────────────────

def _morning_research_tasks() -> list[dict]:
    """Define the parallel research tasks that feed the morning briefing.

    Each entry: {label, system, prompt, tools}.
    Keep tool lists tight — fewer tools, less prompt bloat, faster execution.
    """
    return [
        {
            "label": "inbox",
            "tools": ["list_emails", "read_email", "search_whatsapp", "get_whatsapp_summary"],
            "system": (
                "You are an inbox researcher for the user's morning briefing. "
                "Output ONLY a tight summary, no preamble. Highlight items needing action."
            ),
            "prompt": (
                "Summarise overnight: important unread emails from the last 24h "
                "(all Gmail accounts) and WhatsApp highlights. Cross-reference where "
                "useful. Output 5-10 lines max."
            ),
        },
        {
            "label": "calendar",
            "tools": ["get_calendar", "recall"],
            "system": (
                "You are a calendar/meeting researcher for the user's morning briefing. "
                "Output ONLY the synthesis, no preamble."
            ),
            "prompt": (
                "Summarise today's calendar events across all accounts. For each "
                "meeting with a person, briefly note relevant context (recall). "
                "Output 5-10 lines max."
            ),
        },
        {
            "label": "ops",
            "tools": ["list_todos", "recall"],
            "system": (
                "You are an operations researcher for the user's morning briefing. "
                "Output ONLY the synthesis, no preamble."
            ),
            "prompt": (
                "Summarise pending todos (sorted by priority) and recall any memories "
                "about deadlines, follow-ups, or project commitments due today. "
                "Output 5-10 lines max."
            ),
        },
    ]


def _evening_research_tasks() -> list[dict]:
    """Parallel research tasks for the evening wrap-up."""
    return [
        {
            "label": "today_recap",
            "tools": ["get_calendar", "list_emails", "search_whatsapp"],
            "system": "You are a daily recap researcher. Output ONLY the synthesis, no preamble.",
            "prompt": (
                "Recap today: completed calendar events, notable emails (all accounts), "
                "and WhatsApp highlights — key conversations, decisions, action items. "
                "Output 5-10 lines max."
            ),
        },
        {
            "label": "tomorrow_preview",
            "tools": ["get_calendar", "recall"],
            "system": "You are a tomorrow-preview researcher. Output ONLY the synthesis, no preamble.",
            "prompt": (
                "Preview tomorrow's calendar (all accounts). For each meeting, note any "
                "relevant context from memory. Output 5-10 lines max."
            ),
        },
        {
            "label": "todos_status",
            "tools": ["list_todos"],
            "system": "You are a todos status researcher. Output ONLY the synthesis, no preamble.",
            "prompt": "Show today's todo progress — what's done versus still pending. Output 5 lines max.",
        },
    ]


async def _run_research_tasks(tasks: list[dict]) -> dict[str, str]:
    """Run research tasks in parallel and collect their outputs."""
    assert isinstance(tasks, list) and tasks, "tasks must be non-empty"

    async def _one(task: dict) -> tuple[str, str]:
        label = task["label"]
        try:
            result = await _run_proactive_subagent(
                system=task["system"] + _feedback_context(),
                prompt=task["prompt"],
                allowed_tools=task["tools"],
            )
        except Exception as exc:
            logger.exception(f"Sub-agent '{label}' failed")
            result = f"(research failed: {exc})"
        return (label, result)

    results = await asyncio.gather(*[_one(t) for t in tasks], return_exceptions=False)
    return dict(results)


def _format_module_section(contributions: dict[str, str], section_num: int) -> str:
    """Format module heartbeat contributions as an indented section, or empty if none."""
    if not contributions:
        return ""
    lines = [f"   - {name}: {data}" for name, data in contributions.items()]
    return f"\n{section_num}. Module insights:\n" + "\n".join(lines)


# ── morning briefing ──────────────────────────────────────────────────────────

async def morning_briefing(context):
    """Daily 7:30 AM briefing — parallel research + module signals + final compose."""
    if not OWNER_CHAT_ID:
        logger.warning("OWNER_CHAT_ID not set, skipping morning briefing")
        return
    logger.info("Running morning briefing")
    try:
        research_coro = _run_research_tasks(_morning_research_tasks())
        modules_coro = modules.get_heartbeat_contributions()
        with feedback.proactive_scope():
            research, module_contribs = await asyncio.gather(research_coro, modules_coro)

        sections = [
            f"1. Inbox + WhatsApp:\n{research.get('inbox', '(no data)')}",
            f"2. Calendar today:\n{research.get('calendar', '(no data)')}",
            f"3. Operations:\n{research.get('ops', '(no data)')}",
        ]
        sections.append(_format_module_section(module_contribs, 4))

        compose_prompt = (
            "[HEARTBEAT] Compose my morning briefing from the pre-researched sections "
            "below. Synthesise into a natural, concise briefing. Correlate across "
            "channels where useful. Start with a time-appropriate greeting. Do NOT "
            "re-fetch this data — it's already current.\n\n" + "\n\n".join(sections)
        )
        response, _ = await agent.handle_message(OWNER_CHAT_ID, compose_prompt + _feedback_context(), allowed_tools=[])
        await _send_heartbeat(context, response)
    except Exception:
        logger.exception("Morning briefing failed")


# ── evening wrap-up ───────────────────────────────────────────────────────────

async def evening_wrapup(context):
    """Daily 9:00 PM wrap-up — parallel research + module signals + final compose."""
    if not OWNER_CHAT_ID:
        logger.warning("OWNER_CHAT_ID not set, skipping evening wrap-up")
        return
    logger.info("Running evening wrap-up")
    try:
        research_coro = _run_research_tasks(_evening_research_tasks())
        modules_coro = modules.get_heartbeat_contributions()
        with feedback.proactive_scope():
            research, module_contribs = await asyncio.gather(research_coro, modules_coro)

        sections = [
            f"1. Today recap:\n{research.get('today_recap', '(no data)')}",
            f"2. Tomorrow preview:\n{research.get('tomorrow_preview', '(no data)')}",
            f"3. Todos status:\n{research.get('todos_status', '(no data)')}",
        ]
        sections.append(_format_module_section(module_contribs, 4))

        compose_prompt = (
            "[HEARTBEAT] Compose my evening wrap-up from the pre-researched sections "
            "below. Keep it concise — highlight what matters. Do NOT re-fetch this "
            "data — it's already current.\n\n" + "\n\n".join(sections)
        )
        if datetime.now(TZ).weekday() == 6:  # Sunday: close the week, zero ceremony
            compose_prompt += (
                "\n\nIt's Sunday — also close the week in one warm breath: what actually "
                "moved this week, and the ONE thing that matters most next week. 3-4 lines, "
                "no headers, no ritual — just you leveling with me."
            )
        response, _ = await agent.handle_message(OWNER_CHAT_ID, compose_prompt + _feedback_context(), allowed_tools=[])
        await _send_heartbeat(context, response)
    except Exception:
        logger.exception("Evening wrap-up failed")


# ── email triage ──────────────────────────────────────────────────────────────

async def email_triage(context):
    """3x daily — find emails needing the user's reply, track each as a todo with context."""
    if not OWNER_CHAT_ID:
        logger.warning("OWNER_CHAT_ID not set, skipping email triage")
        return
    logger.info("Running email triage")
    try:
        result = await _run_proactive_subagent(
            system=(
                "You are an email triage agent for the user. You scan their Gmail accounts "
                "for emails that genuinely need a reply from the user and track each one as "
                "a to-do. Be selective — newsletters, notifications, "
                "receipts, calendar invites, FYIs and cold outreach the user would ignore do NOT "
                "need replies." + _feedback_context()
            ),
            prompt=(
                "1. List recent emails: query 'is:inbox newer_than:1d', max_results 30, all accounts. "
                "If an account is unavailable, report incomplete coverage; never treat it as empty.\n"
                "2. Call list_todos with status 'all' and note existing reply tasks so "
                "you never create a duplicate for the same email or thread.\n"
                "3. Read each plausible thread with read_email, which supplies its Source-ID and latest reply state. "
                "Skip threads where the latest delivered message is from the user, and dismissed sources. "
                "Only create tasks when an incoming message contains a concrete request the user needs to answer.\n"
                "4. For each email needing a reply and not already tracked, call "
                "add_todo: task 'Reply to <sender>: <subject>', priority by urgency, "
                "due_date if the email implies a deadline, project 'Email replies', source_id exactly "
                "the thread Source-ID (gmail:account:thread_id), and notes with 1-3 lines of "
                "context — who's asking, what they want, key dates, and the email id. "
                "The same source updates its existing task; do not create alternative source IDs.\n"
                "5. Finish with a one-line-per-todo summary of what you added. If you added "
                "nothing, the summary is just NO_ACTION (the INITIATIVE_FLAG line from step 6 "
                "may still follow it).\n"
                "6. SEPARATELY: if any email implies something bigger than a reply — a hard "
                "deadline, a risk to a commitment, a significant opportunity, or a billing "
                "anomaly (unexpected charge, cost spike, failed payment, usage alert) — append "
                "one final line: INITIATIVE_FLAG: <one-line reason>. At most one flag; omit it "
                "if nothing qualifies."
            ),
            allowed_tools=["list_emails", "read_email", "list_todos", "add_todo"],
            max_iterations=15,
        )
        flag = protagonism.extract_flag(result)
        if flag:
            # application.create_task keeps a strong reference (bare create_task can be GC'd)
            context.application.create_task(
                protagonism.scout_now(context, trigger=f"email triage: {flag}")
            )
            result = protagonism.strip_flag(result)
        if not result or result.startswith("(sub-agent"):
            logger.warning(f"Email triage: no usable sub-agent text — skipping relay ({result!r:.80})")
            return
        if result.strip().upper().startswith("NO_ACTION"):
            logger.info("Email triage: nothing to add")
            return
        response, _ = await agent.handle_message(
            OWNER_CHAT_ID,
            "[HEARTBEAT] Email triage just ran and added these reply-todos:\n"
            f"{result}\n\n"
            "Relay this to me as a brief note — what needs my reply and how urgent. "
            "Do NOT re-fetch this data — it's already current." + _feedback_context(),
            allowed_tools=[],
        )
        await _send_heartbeat(context, response)
    except Exception:
        logger.exception("Email triage failed")


# ── pre-meeting alerts ────────────────────────────────────────────────────────

def _reset_alerts_if_new_day() -> None:
    """Clear the alerted-event set when the date rolls over."""
    global _last_reset_date
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if today != _last_reset_date:
        _alerted_events.clear()
        _last_reset_date = today


def _collect_meeting_alerts(events: list[dict], now: datetime) -> dict[str, str]:
    """Return formatted alert lines for events starting within the alert window."""
    pending: dict[str, str] = {}
    for event in events:
        event_id = event.get("id", "")
        if not event_id:
            continue

        ref = f"calendar:{event.get('_account', 'personal')}:{event_id}"
        if ref in _alerted_events:
            continue
        feedback.register_item(ref, event.get("summary", "(no title)"))
        if feedback.is_suppressed(ref, event.get("summary", "")):
            continue

        raw_start = event.get("start", {}).get("dateTime")
        if not raw_start:
            continue  # skip all-day events

        start = datetime.fromisoformat(raw_start)
        if start.tzinfo is None:
            start = start.replace(tzinfo=TZ)

        minutes_until = (start - now).total_seconds() / 60
        if 0 < minutes_until <= PRE_MEETING_ALERT_WINDOW_MIN:
            summary = event.get("summary", "(no title)")
            acct = event.get("_account", "personal")
            pending[ref] = (
                f"'{summary}' starts in {int(minutes_until)} min "
                f"at {start.strftime('%H:%M')} (calendar: {acct}) [ref: {ref}]"
            )
    return pending


async def check_upcoming_meetings(context):
    """Every 5 min — check for meetings starting in ~15 min and send batched alert."""
    _reset_alerts_if_new_day()

    try:
        events = await tools.get_upcoming_events(minutes=MEETING_LOOKAHEAD_MIN)
    except Exception:
        logger.exception("Failed to check upcoming meetings")
        return

    pending_alerts = _collect_meeting_alerts(events, datetime.now(TZ))
    if not pending_alerts:
        return

    logger.info(f"Meeting alerts: {len(pending_alerts)} upcoming")
    try:
        alert_text = "\n".join(f"- {a}" for a in pending_alerts.values())
        response, _ = await agent.handle_message(
            OWNER_CHAT_ID,
            f"[HEARTBEAT] Meeting alert — upcoming meetings:\n{alert_text}\n\n"
            "Recall any relevant context about these meetings or the people involved, "
            "then send me a brief, helpful heads-up for each." + _feedback_context(),
            allowed_tools=["recall"],
        )
        if protagonism._is_failure_sentinel(response):
            raise RuntimeError("Meeting alert composition failed; leaving it pending.")
        notice_ref = next(iter(pending_alerts)) if len(pending_alerts) == 1 else ""
        await _send_heartbeat(context, response, notice_ref=notice_ref)
        _alerted_events.update(pending_alerts)
    except Exception:
        logger.exception("Meeting alert failed")
