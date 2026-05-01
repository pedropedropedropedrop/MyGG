"""Meeting note-taker — transcribe, contextualize, extract action items."""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic

import tools
from constants import CLAUDE_MAX_TOKENS, MAX_TRANSCRIPT_CHARS, CLAUDE_API_TIMEOUT_SECONDS, CLAUDE_CLARIFY_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

TZ = ZoneInfo(os.getenv("TIMEZONE", "America/New_York"))
MEETINGS_DIR = Path(__file__).parent.parent / "meeting_transcripts"
MEETINGS_DIR.mkdir(exist_ok=True)

_client = anthropic.AsyncAnthropic()
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")


# ── Database ─────────────────────────────────────────────────────────────────

def _init_meetings_table():
    conn = tools.get_db()
    try:
        conn.executescript("""
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

try:
    _init_meetings_table()
except Exception:
    logger.exception("meetings: failed to initialize DB table")


def _save_transcript(meeting_id: int, transcript: str) -> str:
    path = MEETINGS_DIR / f"meeting_{meeting_id}.txt"
    path.write_text(transcript, encoding="utf-8")
    return str(path)


def _create_meeting(date: str, duration: float | None = None, language: str | None = None) -> int:
    conn = tools.get_db()
    try:
        cur = conn.execute(
            "INSERT INTO meeting_notes (date, duration_seconds, language, status) VALUES (?, ?, ?, 'processing')",
            (date, duration, language),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _update_meeting(meeting_id: int, **fields):
    conn = tools.get_db()
    try:
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [meeting_id]
        conn.execute(f"UPDATE meeting_notes SET {sets} WHERE id = ?", vals)
        conn.commit()
    finally:
        conn.close()


def _get_meeting(meeting_id: int) -> dict | None:
    conn = tools.get_db()
    try:
        row = conn.execute("SELECT * FROM meeting_notes WHERE id = ?", (meeting_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get_recent_meetings(days: int = 7) -> list[dict]:
    conn = tools.get_db()
    try:
        cutoff = (datetime.now(TZ) - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT * FROM meeting_notes WHERE date >= ? ORDER BY date DESC LIMIT 20",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Transcription ────────────────────────────────────────────────────────────

def _fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


async def _transcribe_audio(file_path: str) -> tuple[str, float]:
    """Transcribe using the proven tools.transcribe_audio path.

    Note: tools.transcribe_audio has its own 60s timeout — we use a longer
    outer timeout here to allow for large files (the inner timeout is per-chunk).
    """
    transcript = await tools.transcribe_audio(file_path)
    # Duration is logged by faster-whisper but not returned — estimate from transcript length
    # ~150 words per minute of speech, ~5 chars per word
    estimated_duration = len(transcript) / (150 * 5 / 60)  # rough estimate in seconds
    return transcript, estimated_duration


# ── Context Gathering ────────────────────────────────────────────────────────

async def _gather_context(meeting_date: str) -> dict:
    context = {
        "calendar_events": "",
        "email_context": "",
        "memory_context": "",
    }

    try:
        context["calendar_events"] = await tools.get_calendar(meeting_date[:10], days=1)
    except Exception:
        logger.warning("Could not fetch calendar for meeting context")

    try:
        context["memory_context"] = await asyncio.wait_for(
            tools.recall(f"meeting {meeting_date[:10]}"), timeout=15,
        )
    except Exception:
        logger.warning("Could not recall meeting memories")

    try:
        context["email_context"] = await tools.list_emails("newer_than:2d", max_results=5)
    except Exception:
        logger.warning("Could not fetch recent emails for meeting context")

    return context


# ── Claude Processing ────────────────────────────────────────────────────────

MEETING_EXTRACTION_PROMPT = """You are processing a meeting transcript for the user's personal AI assistant.

## Available Context
{context}

## Instructions
Analyze the transcript and produce a structured JSON response with these fields:

1. "title": A short descriptive title for the meeting (infer from content)
2. "language": Primary language of the meeting (ISO code or "mixed")
3. "summary": 3-5 sentence summary of what was discussed. Write in the same language as the meeting.
4. "decisions": List of decisions made (strings). Empty list if none.
5. "action_items": List of objects, each with:
   - "task": what needs to be done
   - "assignee": who should do it (use "user" if it's for the user, name if identified, "unknown" if unclear)
   - "deadline": deadline if mentioned, null otherwise
   - "priority": "high", "medium", or "low"
6. "participants": List of objects, each with:
   - "name": name or identifier used in the meeting
   - "role": role/context if you can infer it, null otherwise
   - "known": true if this person appears in the context (calendar, emails, memories), false if new
7. "clarifications_needed": List of questions about things you're unsure of. Examples:
   - Unknown participants: "I heard someone named 'Carlos' - who is he? What's his role?"
   - Ambiguous references: "They discussed 'the proposal' - which proposal is this?"
   - Unclear commitments: "Someone said they'd 'handle the contract' - was that the user or the other person?"
   Only ask about things that matter for accurate notes. Don't ask about small talk.
8. "follow_up_emails": List of objects for suggested follow-up emails:
   - "to": recipient (name or email if known)
   - "subject": suggested subject line
   - "purpose": one-line description of what the email should cover

Respond with ONLY the JSON object, no markdown fences, no explanation."""


async def _process_with_claude(transcript: str, context: dict) -> dict:
    context_text = ""
    if context.get("calendar_events"):
        context_text += f"### Today's Calendar Events\n{context['calendar_events']}\n\n"
    if context.get("email_context"):
        context_text += f"### Recent Emails (last 48h)\n{context['email_context']}\n\n"
    if context.get("memory_context") and context["memory_context"] != "No relevant memories found.":
        context_text += f"### Relevant Memories\n{context['memory_context']}\n\n"

    prompt = MEETING_EXTRACTION_PROMPT.format(context=context_text or "(No additional context available)")

    max_transcript_chars = MAX_TRANSCRIPT_CHARS
    if len(transcript) > max_transcript_chars:
        transcript = transcript[:max_transcript_chars] + "\n\n... (transcript truncated)"

    for attempt in range(3):
        try:
            response = await asyncio.wait_for(
                _client.messages.create(
                    model=MODEL,
                    max_tokens=CLAUDE_MAX_TOKENS,
                    system=prompt,
                    messages=[{"role": "user", "content": f"## Meeting Transcript\n\n{transcript}"}],
                ),
                timeout=CLAUDE_API_TIMEOUT_SECONDS,  # 2 min — meetings are large prompts
            )
            text = response.content[0].text.strip()
            # LOGIC-8 FIX: robust markdown fence stripping
            import re
            text = re.sub(r'^```[a-z]*\n', '', text)
            text = re.sub(r'\n?```$', '', text)
            text = text.strip()
            result = json.loads(text)
            assert isinstance(result, dict), f"Claude returned non-dict: {type(result)}"
            assert "summary" in result, f"Claude response missing 'summary' key: {list(result.keys())}"
            return result
        except anthropic.APIStatusError as e:
            if e.status_code in (429, 529) and attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            raise
        except json.JSONDecodeError:
            logger.warning(f"Claude returned non-JSON response (attempt {attempt + 1})")
            if attempt == 2:
                return {
                    "title": "Meeting Notes",
                    "language": "en",
                    "summary": text[:500] if text else "Processing failed",
                    "decisions": [],
                    "action_items": [],
                    "participants": [],
                    "clarifications_needed": ["I couldn't structure the notes properly. Please review the raw transcript."],
                    "follow_up_emails": [],
                }
    # All retries exhausted on rate-limit — raise instead of returning empty dict
    raise RuntimeError("Meeting processing failed: API rate-limited after 3 attempts")


# ── Tool Definitions ─────────────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "process_meeting",
        "description": (
            "Process a meeting audio recording. Transcribes the audio, cross-references "
            "with calendar/email/memory, extracts summary, decisions, action items, and "
            "identifies participants. Use this when the user sends an audio file and says "
            "it's a meeting recording, or when the caption mentions 'meeting'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the audio file on disk",
                },
                "meeting_date": {
                    "type": "string",
                    "description": "Date of the meeting in YYYY-MM-DD format. Defaults to today.",
                },
                "context_hint": {
                    "type": "string",
                    "description": "Optional context from the user about the meeting (who it was with, what project, etc.)",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "meeting_clarify",
        "description": (
            "Provide answers to clarifying questions from a meeting that was just processed. "
            "Call this after the user answers the clarification questions about a meeting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "meeting_id": {
                    "type": "integer",
                    "description": "The meeting ID from the process_meeting result",
                },
                "answers": {
                    "type": "string",
                    "description": "The user's answers to the clarifying questions, as free text",
                },
            },
            "required": ["meeting_id", "answers"],
        },
    },
    {
        "name": "get_meeting_notes",
        "description": (
            "Retrieve notes from a past meeting by ID or search recent meetings. "
            "Use when the user asks about a previous meeting's notes, decisions, or action items."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "meeting_id": {
                    "type": "integer",
                    "description": "Specific meeting ID to retrieve",
                },
                "search_days": {
                    "type": "integer",
                    "description": "Search meetings from last N days. Defaults to 7.",
                },
            },
        },
    },
]


async def execute_tool(name: str, input_data: dict) -> str:
    match name:
        case "process_meeting":
            return await _handle_process_meeting(input_data)
        case "meeting_clarify":
            return await _handle_clarify(input_data)
        case "get_meeting_notes":
            return await _handle_get_notes(input_data)
        case _:
            return f"Unknown tool: {name}"


# ── Pipeline ─────────────────────────────────────────────────────────────────

async def _create_todos_from_action_items(action_items: list[dict], meeting_id: int) -> int:
    """Create todo items from meeting action items assigned to the user."""
    todos_created = 0
    for item in action_items:
        if item.get("assignee", "").lower() in ("user", "me", "self", "i"):
            try:
                await tools.add_todo(
                    item["task"],
                    priority=item.get("priority", "medium"),
                    due_date=item.get("deadline"),
                )
                todos_created += 1
            except Exception:
                logger.warning(f"Failed to create todo: {item['task']}")
    return todos_created


async def _handle_process_meeting(input_data: dict) -> str:
    file_path = input_data["file_path"]
    meeting_date = input_data.get("meeting_date") or datetime.now(TZ).strftime("%Y-%m-%d")
    context_hint = input_data.get("context_hint", "")

    meeting_id = _create_meeting(meeting_date)
    logger.info(f"Processing meeting #{meeting_id} from {file_path}")

    try:
        transcript, duration = await _transcribe_audio(file_path)
    except asyncio.TimeoutError:
        _update_meeting(meeting_id, status="failed")
        return "Transcription timed out (10 min limit). The audio file may be too long."
    except Exception as e:
        _update_meeting(meeting_id, status="failed")
        return f"Transcription failed: {e}"

    _update_meeting(meeting_id, duration_seconds=duration)
    transcript_path = _save_transcript(meeting_id, transcript)
    _update_meeting(meeting_id, transcript_path=transcript_path)
    logger.info(f"Meeting #{meeting_id}: transcribed {duration:.0f}s -> {len(transcript)} chars")

    context = await _gather_context(meeting_date)
    if context_hint:
        context["memory_context"] = (context.get("memory_context") or "") + f"\n\nUser's context: {context_hint}"

    try:
        result = await _process_with_claude(transcript, context)
    except Exception as e:
        _update_meeting(meeting_id, status="failed")
        return f"Claude processing failed: {e}"

    _update_meeting(
        meeting_id,
        title=result.get("title", "Meeting Notes"),
        language=result.get("language", "en"),
        summary=result.get("summary", ""),
        decisions=json.dumps(result.get("decisions", []), ensure_ascii=False),
        action_items=json.dumps(result.get("action_items", []), ensure_ascii=False),
        participants=json.dumps(result.get("participants", []), ensure_ascii=False),
        clarifications=json.dumps(result.get("clarifications_needed", []), ensure_ascii=False),
        status="needs_clarification" if result.get("clarifications_needed") else "complete",
    )

    todos_created = await _create_todos_from_action_items(result.get("action_items", []), meeting_id)

    try:
        memory_text = f"Meeting on {meeting_date}: {result.get('title', 'Untitled')}. {result.get('summary', '')}"
        participants_str = ", ".join(p.get("name", "?") for p in result.get("participants", []))
        if participants_str:
            memory_text += f" Participants: {participants_str}."
        decisions = result.get("decisions", [])
        if decisions:
            memory_text += " Decisions: " + "; ".join(decisions) + "."
        await tools.remember(memory_text, tags=["meeting", meeting_date])
    except Exception:
        logger.warning("Failed to store meeting in mem0")

    return _format_meeting_response(meeting_id, result, duration, todos_created)


def _format_meeting_response(meeting_id: int, result: dict, duration: float, todos_created: int) -> str:
    lines = [f"Meeting #{meeting_id}: {result.get('title', 'Meeting Notes')}"]
    lines.append(f"Duration: {_fmt_time(duration)} | Language: {result.get('language', '?')}")
    lines.append("")

    lines.append("SUMMARY")
    lines.append(result.get("summary", "(no summary)"))
    lines.append("")

    participants = result.get("participants", [])
    if participants:
        lines.append("PARTICIPANTS")
        for p in participants:
            known = "known" if p.get("known") else "NEW"
            role = f" - {p['role']}" if p.get("role") else ""
            lines.append(f"  - {p.get('name', '?')}{role} [{known}]")
        lines.append("")

    decisions = result.get("decisions", [])
    if decisions:
        lines.append("DECISIONS")
        for d in decisions:
            lines.append(f"  - {d}")
        lines.append("")

    action_items = result.get("action_items", [])
    if action_items:
        lines.append(f"ACTION ITEMS ({todos_created} added as todos)")
        for item in action_items:
            assignee = item.get("assignee", "?")
            deadline = f" (due: {item['deadline']})" if item.get("deadline") else ""
            lines.append(f"  [{item.get('priority', 'med')}] {item['task']} -> {assignee}{deadline}")
        lines.append("")

    emails = result.get("follow_up_emails", [])
    if emails:
        lines.append("SUGGESTED FOLLOW-UP EMAILS")
        for e in emails:
            lines.append(f"  - To: {e.get('to', '?')} | {e.get('subject', '?')}")
            lines.append(f"    {e.get('purpose', '')}")
        lines.append("")

    clarifications = result.get("clarifications_needed", [])
    if clarifications:
        lines.append("QUESTIONS FOR YOU")
        for i, q in enumerate(clarifications, 1):
            lines.append(f"  {i}. {q}")
        lines.append("")
        lines.append(f"Answer my questions and I'll refine the notes. (Meeting ID: {meeting_id})")

    return "\n".join(lines)


def _parse_clarification_response(text: str) -> dict:
    """Parse Claude's clarification response, with fallback for non-JSON."""
    import re
    text = re.sub(r'^```[a-z]*\n', '', text)
    text = re.sub(r'\n?```$', '', text)
    text = text.strip()
    return json.loads(text)


async def _handle_clarify(input_data: dict) -> str:
    meeting_id = input_data["meeting_id"]
    answers = input_data["answers"]

    meeting = _get_meeting(meeting_id)
    if not meeting:
        return f"Meeting #{meeting_id} not found."

    existing_clarifications = json.loads(meeting.get("clarifications") or "[]")
    existing_participants = json.loads(meeting.get("participants") or "[]")
    existing_summary = meeting.get("summary", "")

    refine_prompt = f"""You previously processed a meeting and had these clarifying questions:
{json.dumps(existing_clarifications, indent=2)}

The user answered:
{answers}

Current meeting summary: {existing_summary}
Current participants: {json.dumps(existing_participants, indent=2)}

Update the meeting information based on the user's answers. Return JSON with:
- "updated_participants": updated participant list with any new info
- "updated_summary": refined summary if the answers change understanding
- "new_memories": list of important facts to remember about people/projects mentioned
- "remaining_questions": any follow-up questions (empty list if all is clear)"""

    try:
        response = await asyncio.wait_for(
            _client.messages.create(
                model=MODEL, max_tokens=2048,
                messages=[{"role": "user", "content": refine_prompt}],
            ),
            timeout=CLAUDE_CLARIFY_TIMEOUT_SECONDS,  # 1 min for clarification refinement
        )
        updates = _parse_clarification_response(response.content[0].text.strip())
    except Exception as e:
        return f"Failed to process clarifications: {e}"

    if updates.get("updated_participants"):
        _update_meeting(meeting_id, participants=json.dumps(updates["updated_participants"], ensure_ascii=False))
    if updates.get("updated_summary"):
        _update_meeting(meeting_id, summary=updates["updated_summary"])

    remaining = updates.get("remaining_questions", [])
    _update_meeting(
        meeting_id,
        clarifications=json.dumps(remaining, ensure_ascii=False),
        status="needs_clarification" if remaining else "complete",
    )

    for mem in updates.get("new_memories", []):
        try:
            await tools.remember(mem, tags=["meeting", "person"])
        except Exception:
            logger.warning(f"Failed to store meeting memory: {mem[:80]}", exc_info=True)

    if remaining:
        lines = ["Thanks! I've updated the notes. A few more questions:"]
        for i, q in enumerate(remaining, 1):
            lines.append(f"  {i}. {q}")
        lines.append(f"\n(Meeting ID: {meeting_id})")
        return "\n".join(lines)

    return f"Meeting #{meeting_id} notes finalized. All clarifications resolved - I've remembered the people and context for next time."


async def _handle_get_notes(input_data: dict) -> str:
    meeting_id = input_data.get("meeting_id")
    search_days = input_data.get("search_days", 7)

    if meeting_id:
        meeting = _get_meeting(meeting_id)
        if not meeting:
            return f"Meeting #{meeting_id} not found."
        return _format_stored_meeting(meeting)

    meetings = _get_recent_meetings(search_days)
    if not meetings:
        return f"No meetings in the last {search_days} days."

    lines = [f"Recent meetings (last {search_days} days):"]
    for m in meetings:
        status = m.get("status", "?")
        title = m.get("title") or "Untitled"
        date = m.get("date", "?")[:10]
        dur = _fmt_time(m["duration_seconds"]) if m.get("duration_seconds") else "?"
        lines.append(f"  #{m['id']} [{date}] {title} ({dur}) - {status}")
    return "\n".join(lines)


def _format_stored_meeting(m: dict) -> str:
    lines = [f"Meeting #{m['id']}: {m.get('title', 'Untitled')}"]
    lines.append(f"Date: {m.get('date', '?')[:10]} | Duration: {_fmt_time(m['duration_seconds']) if m.get('duration_seconds') else '?'}")
    lines.append("")

    if m.get("summary"):
        lines.append("SUMMARY")
        lines.append(m["summary"])
        lines.append("")

    participants = json.loads(m.get("participants") or "[]")
    if participants:
        lines.append("PARTICIPANTS")
        for p in participants:
            role = f" - {p['role']}" if p.get("role") else ""
            lines.append(f"  - {p.get('name', '?')}{role}")
        lines.append("")

    decisions = json.loads(m.get("decisions") or "[]")
    if decisions:
        lines.append("DECISIONS")
        for d in decisions:
            lines.append(f"  - {d}")
        lines.append("")

    action_items = json.loads(m.get("action_items") or "[]")
    if action_items:
        lines.append("ACTION ITEMS")
        for item in action_items:
            assignee = item.get("assignee", "?")
            lines.append(f"  - {item['task']} -> {assignee}")
        lines.append("")

    return "\n".join(lines)


async def heartbeat_contributions() -> str | None:
    try:
        conn = tools.get_db()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM meeting_notes WHERE status = 'needs_clarification'"
            ).fetchone()[0]
        finally:
            conn.close()
        if count > 0:
            return f"{count} meeting(s) need your clarification - ask me about them"
    except Exception:
        logger.warning("Meeting heartbeat contribution failed", exc_info=True)
    return None
