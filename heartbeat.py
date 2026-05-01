"""Proactive heartbeat system — morning briefings, meeting alerts, evening wrap-ups."""

import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import agent
import tools
import modules
from utils import split_long_message

logger = logging.getLogger(__name__)

TZ = ZoneInfo(os.getenv("TIMEZONE", "America/New_York"))
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "0"))

# Pre-meeting alert state
_alerted_events: set[str] = set()
_last_reset_date: str = ""


async def _send_heartbeat(context, text: str):
    """Send a heartbeat message to the owner, splitting if needed."""
    if not OWNER_CHAT_ID:
        logger.warning("OWNER_CHAT_ID not set, skipping heartbeat")
        return

    for chunk in split_long_message(text):
        await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=chunk)


async def morning_briefing(context):
    """Daily 7:30 AM briefing — calendar, todos, emails, weather, memory context."""
    if not OWNER_CHAT_ID:
        logger.warning("OWNER_CHAT_ID not set, skipping morning briefing")
        return
    logger.info("Running morning briefing")
    try:
        # Collect module heartbeat data
        contributions = await modules.get_heartbeat_contributions()
        module_section = ""
        if contributions:
            module_section = "\n".join(
                f"   - {name}: {data}" for name, data in contributions.items()
            )
            module_section = f"\n6. Module insights:\n{module_section}"

        response, _ = await agent.handle_message(
            OWNER_CHAT_ID,
            f"[HEARTBEAT] Generate {agent.USER_NAME}'s morning briefing. Use your tools to gather:\n"
            "1. Today's calendar events (all accounts)\n"
            f"2. {agent.USER_NAME}'s pending todos sorted by priority\n"
            "3. Important unread emails (all accounts, last 24h)\n"
            "4. WhatsApp highlights from overnight — check for anything to know or act on\n"
            f"5. Recall any memories about today's plans, deadlines, or follow-ups\n"
            f"{module_section}\n\n"
            "Synthesize everything into a natural, concise morning briefing. "
            "Correlate across channels — if someone messaged on WhatsApp about a calendar event, connect the dots. "
            "Start with an appropriate greeting for the time of day."
        )
        await _send_heartbeat(context, response)
    except Exception:
        logger.exception("Morning briefing failed")


async def evening_wrapup(context):
    """Daily 9:00 PM wrap-up — recap today, preview tomorrow, email/todo status."""
    if not OWNER_CHAT_ID:
        logger.warning("OWNER_CHAT_ID not set, skipping evening wrap-up")
        return
    logger.info("Running evening wrap-up")
    try:
        contributions = await modules.get_heartbeat_contributions()
        module_section = ""
        if contributions:
            module_section = "\n".join(
                f"   - {name}: {data}" for name, data in contributions.items()
            )
            module_section = f"\n6. Module insights:\n{module_section}"

        response, _ = await agent.handle_message(
            OWNER_CHAT_ID,
            f"[HEARTBEAT] Generate {agent.USER_NAME}'s evening wrap-up. Use your tools to gather:\n"
            "1. Recap of today's calendar events\n"
            "2. Preview of tomorrow's calendar (all accounts)\n"
            "3. Any unread emails from today (all accounts)\n"
            "4. WhatsApp highlights from today — key conversations, decisions, action items\n"
            "5. Todo progress — what's done vs still pending\n"
            f"{module_section}\n\n"
            "Synthesize into a concise evening summary. "
            "Keep it brief — highlight what matters."
        )
        await _send_heartbeat(context, response)
    except Exception:
        logger.exception("Evening wrap-up failed")


async def check_upcoming_meetings(context):
    """Every 5 min — check for meetings starting in ~15 min and send batched alert."""
    global _last_reset_date

    # Reset alerted set daily
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if today != _last_reset_date:
        _alerted_events.clear()
        _last_reset_date = today

    try:
        events = await tools.get_upcoming_events(minutes=20)
    except Exception:
        logger.exception("Failed to check upcoming meetings")
        return

    now = datetime.now(TZ)

    # Collect all meetings that need alerting
    pending_alerts = []
    for event in events:
        event_id = event.get("id", "")
        if not event_id or event_id in _alerted_events:
            continue

        raw_start = event.get("start", {}).get("dateTime")
        if not raw_start:
            continue  # skip all-day events

        start = datetime.fromisoformat(raw_start)
        if start.tzinfo is None:
            start = start.replace(tzinfo=TZ)

        minutes_until = (start - now).total_seconds() / 60

        if 0 < minutes_until <= 15:
            _alerted_events.add(event_id)
            summary = event.get("summary", "(no title)")
            acct = event.get("_account", "personal")
            pending_alerts.append(
                f"'{summary}' starts in {int(minutes_until)} min at {start.strftime('%H:%M')} (calendar: {acct})"
            )

    # Send a single batched alert if there are meetings
    if pending_alerts:
        logger.info(f"Meeting alerts: {len(pending_alerts)} upcoming")
        try:
            alert_text = "\n".join(f"- {a}" for a in pending_alerts)
            response, _ = await agent.handle_message(
                OWNER_CHAT_ID,
                f"[HEARTBEAT] Meeting alert — upcoming meetings:\n{alert_text}\n\n"
                "Recall any relevant context about these meetings or the people involved, "
                "then send a brief, helpful heads-up for each."
            )
            await _send_heartbeat(context, response)
        except Exception:
            logger.exception("Meeting alert failed")
