"""Claude agent with tool use for the MyGG personal AI assistant."""

import asyncio
import os
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic

import tools
import modules
import json as _json
from constants import CLAUDE_MAX_TOKENS, TOOL_RESULT_MAX_CHARS, MAX_HISTORY, LOCK_ACQUIRE_TIMEOUT_SECONDS, MEMORY_RECALL_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

USER_NAME = os.getenv("USER_NAME", "user")
TZ = ZoneInfo(os.getenv("TIMEZONE", "America/New_York"))
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
MODEL_OPUS = os.getenv("CLAUDE_MODEL_OPUS", "claude-opus-4-6")
client = anthropic.AsyncAnthropic()

# Path to the optional personality file (next to this script).
PERSONALITY_FILE = Path(__file__).parent / "personality.md"

# In-memory conversation history per chat
conversations: dict[int, list] = {}

# Per-chat model overrides (chat_id -> model string)
# Bounded: entries evicted alongside conversations in _evict_stale_conversations
model_overrides: dict[int, str] = {}

# Store last real user message per chat (not tool_results) for context-sensitive checks
_last_user_message: dict[int, str] = {}

# Per-chat locks to prevent concurrent access (heartbeat + user message race)
# Bounded: evicted when MAX_CONVERSATIONS exceeded (see _evict_stale_conversations)
_chat_locks: dict[int, asyncio.Lock] = {}

_MAX_CONVERSATIONS = 50  # Max concurrent chat histories before oldest evicted


def _get_chat_lock(chat_id: int) -> asyncio.Lock:
    # setdefault is atomic in CPython's asyncio single-threaded model
    return _chat_locks.setdefault(chat_id, asyncio.Lock())


def _evict_stale_conversations():
    """Remove oldest conversations when cache exceeds limit."""
    if len(conversations) <= _MAX_CONVERSATIONS:
        return
    excess = len(conversations) - _MAX_CONVERSATIONS
    oldest_keys = list(conversations.keys())[:excess]
    for key in oldest_keys:
        del conversations[key]
        _last_user_message.pop(key, None)
        _chat_locks.pop(key, None)
        model_overrides.pop(key, None)
    logger.info(f"Evicted {excess} stale conversation(s)")


def _trim_history(messages: list):
    """Trim message history to MAX_HISTORY, respecting message-pair boundaries.

    Always ensures the first message after trim is a 'user' role (not a tool_result
    or assistant message mid-turn) to avoid Anthropic API 400 errors.
    """
    if len(messages) <= MAX_HISTORY:
        return
    messages[:] = messages[-MAX_HISTORY:]
    # Ensure we start with a user message (not an assistant or tool_result)
    while messages and messages[0].get("role") != "user":
        messages.pop(0)
    # Remove any orphaned tool_result messages (and subsequent assistant turns)
    # that reference tool_use_ids no longer in history. Loop handles multiple layers.
    while messages:
        content = messages[0].get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict) and content[0].get("type") == "tool_result":
            messages.pop(0)
            while messages and messages[0].get("role") != "user":
                messages.pop(0)
        else:
            break


TOOL_DEFINITIONS = [
    {
        "name": "remember",
        "description": (
            "Store important information to long-term memory. USE THIS PROACTIVELY. "
            f"When {USER_NAME} shares decisions, ideas, mentions people, preferences, project updates, "
            "deadlines, or anything worth remembering — store it without being asked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The information to remember",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for categorization, e.g. 'project:work', 'person:john', 'idea', 'decision'",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "recall",
        "description": "Search long-term memory for relevant information. Use when you need context about past conversations, people, decisions, or projects.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for in memory",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_calendar",
        "description": "View calendar events for a date range. Fetches from all configured Google accounts by default.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format. Defaults to today.",
                },
                "days": {
                    "type": "integer",
                    "description": "Number of days to look ahead. Defaults to 1.",
                },
                "account": {
                    "type": "string",
                    "description": "Which account to fetch from. Defaults to all configured accounts.",
                },
            },
        },
    },
    {
        "name": "create_event",
        "description": (
            f"Create a Google Calendar event. When {USER_NAME} mentions someone to add and you have their "
            "email in memory or context, pass it via attendees. Defaults to the 'personal' account."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title"},
                "start": {
                    "type": "string",
                    "description": "Start time as ISO datetime, e.g. 2026-03-09T14:00:00",
                },
                "end": {
                    "type": "string",
                    "description": "End time as ISO datetime. Defaults to 1 hour after start.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional event description",
                },
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of attendee email addresses to invite",
                },
                "account": {
                    "type": "string",
                    "description": "Which calendar account to use. Defaults to 'personal'.",
                },
            },
            "required": ["title", "start"],
        },
    },
    {
        "name": "add_todo",
        "description": "Add a new todo item.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The task description"},
                "priority": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Priority level. Defaults to medium.",
                },
                "due_date": {
                    "type": "string",
                    "description": "Optional due date in YYYY-MM-DD format",
                },
                "project": {
                    "type": "string",
                    "description": "Optional project tag, e.g. 'work', 'home', 'personal'",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "list_todos",
        "description": "List current todos, optionally filtered.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Filter by project tag",
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "done", "all"],
                    "description": "Filter by status. Defaults to pending.",
                },
                "priority": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Filter by priority",
                },
            },
        },
    },
    {
        "name": "complete_todo",
        "description": "Mark a todo as completed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "todo_id": {"type": "integer", "description": "The todo ID number"},
            },
            "required": ["todo_id"],
        },
    },
    {
        "name": "delete_todo",
        "description": "Delete a todo item.",
        "input_schema": {
            "type": "object",
            "properties": {
                "todo_id": {"type": "integer", "description": "The todo ID number"},
            },
            "required": ["todo_id"],
        },
    },
    # ── Server-side tools (executed by Anthropic, not dispatched locally) ──
    {"type": "web_search_20260209", "name": "web_search"},
    {"type": "web_fetch_20260209", "name": "web_fetch"},
    {"type": "code_execution_20260120", "name": "code_execution"},
    {
        "name": "list_emails",
        "description": "List emails from Gmail inbox. Fetches from all configured accounts by default. Supports Gmail search syntax.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail search query, e.g. 'is:unread', 'from:john', 'subject:invoice', 'newer_than:2d'. Defaults to 'is:inbox'.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max emails to return per account. Defaults to 10.",
                },
                "account": {
                    "type": "string",
                    "description": "Which account to fetch from. Defaults to all configured accounts.",
                },
            },
        },
    },
    {
        "name": "read_email",
        "description": "Read the full content of a specific email by its ID (format: 'account:id').",
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "The email ID from list_emails (format: 'account:msgid')"},
            },
            "required": ["email_id"],
        },
    },
    {
        "name": "send_email",
        "description": (
            f"Send a new email. When {USER_NAME} explicitly asks you to send an email, that IS the confirmation — just send it. "
            "Compose subject and body from conversation context. Defaults to the 'personal' account."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Email body text"},
                "account": {
                    "type": "string",
                    "description": "Which account to send from. Defaults to 'personal'.",
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "reply_email",
        "description": (
            f"Reply to an existing email. When {USER_NAME} asks you to reply, that IS the confirmation — compose and send. "
            "Auto-detects account from email ID."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "The email ID to reply to (format: 'account:msgid')"},
                "body": {"type": "string", "description": "Reply body text"},
            },
            "required": ["email_id", "body"],
        },
    },
    {
        "name": "set_reminder",
        "description": f"Set a reminder that will ping {USER_NAME} on Telegram at the specified time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "What to remind about",
                },
                "when": {
                    "type": "string",
                    "description": "ISO datetime for the reminder, e.g. 2026-03-09T09:00:00",
                },
            },
            "required": ["text", "when"],
        },
    },
    {
        "name": "search_whatsapp",
        "description": "Search WhatsApp messages by text content. Use to find specific conversations, mentions, or information shared on WhatsApp.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for in messages",
                },
                "chat_name": {
                    "type": "string",
                    "description": "Optional: filter by chat/group name",
                },
                "hours_back": {
                    "type": "integer",
                    "description": "How many hours back to search. Defaults to 24.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_whatsapp_summary",
        "description": (
            "Get a summary of recent WhatsApp activity across all chats, or for a specific chat. "
            f"Use during briefings or when {USER_NAME} asks 'what's going on' or 'what did I miss'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "chat_name": {
                    "type": "string",
                    "description": "Optional: specific chat/group name to summarize",
                },
                "hours_back": {
                    "type": "integer",
                    "description": "How many hours back to summarize. Defaults to 24.",
                },
            },
        },
    },
    {
        "name": "send_audio",
        "description": (
            f"Send a voice message to {USER_NAME}. Use when: {USER_NAME} sent a voice note and "
            "a voice reply feels natural, the response is long and better consumed "
            f"as audio, or {USER_NAME} explicitly asks for audio. Always also provide the "
            "text response in your regular reply — audio is supplementary, not a replacement."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": (
                        "The text to speak aloud. Write naturally — this will be spoken, "
                        "so avoid markdown, bullet points, or formatting."
                    ),
                },
            },
            "required": ["text"],
        },
    },
]


def _get_all_tool_definitions() -> list[dict]:
    """Core tools + module tools."""
    return TOOL_DEFINITIONS + modules.get_tool_definitions()


def _prompt_header(now: datetime) -> str:
    """Date/time/greeting guidance section."""
    hour = now.hour
    if hour < 12:
        time_of_day = "morning"
    elif hour < 17:
        time_of_day = "afternoon"
    else:
        time_of_day = "evening"

    return f"""You are {USER_NAME}'s personal AI assistant on Telegram. Be direct, concise, and proactive.

Current time: {now.strftime('%A, %B %d %Y at %H:%M')} ({TZ})
It is currently {time_of_day}. ALWAYS greet appropriately for the time of day — say "Good {time_of_day}" not "Good morning" unless it IS morning (before noon)."""


def _load_personality() -> str:
    """Load the user's personality.md file (if present) for system-prompt context.

    Anything written in personality.md is appended verbatim to the system prompt
    on every message. Keep it focused and concise — see personality.md.example.
    Returns an empty string if the file is missing or unreadable.
    """
    try:
        if PERSONALITY_FILE.exists():
            text = PERSONALITY_FILE.read_text(encoding="utf-8").strip()
            if text:
                return "\n\n" + text
    except Exception:
        logger.exception("Failed to read personality.md")
    return ""


def _prompt_behavior_rules() -> str:
    """Behavioral rules (proactive memory, tools usage, etc.)."""
    return f"""
## Behavior
- Be concise. No fluff. Lead with the answer or action.
- PROACTIVELY use the remember tool. When {USER_NAME} shares decisions, ideas, people, preferences, deadlines, project updates — store them immediately.
- Use tools actively. Don't talk about doing things — DO them.
- You have built-in web search and web fetch — use them freely when {USER_NAME} asks about current info, shares URLs, or you need to look something up.
- You have code execution — use it for calculations, data analysis, charts, or any task that benefits from running real code rather than estimating.
- When asked for a briefing: show calendar, pending todos, recent reminders, and project status from memory.
- You can call multiple tools in one turn when needed.
- Only ask clarifying questions when the answer is TRULY unknowable from the conversation. If {USER_NAME} gave you the info earlier in the chat, USE IT — don't re-ask.

## Context Awareness (CRITICAL)
You MUST use information from the current conversation before asking questions. {USER_NAME} should NEVER have to repeat themselves.

**Emails:** When {USER_NAME} says "send an email about X" and the details of X are in the conversation, compose the email yourself — pick a subject line, write the body from the context you have. Be creative and helpful. Do NOT ask for subject line or body content when you already have the context. {USER_NAME} trusts you to compose well.

**Follow-ups:** When {USER_NAME} says "correct it", "fix it", "send it again", "update it" — look at what you just did in this conversation. You know which email, which event, which person. Don't ask "which email?" or "what needs correcting?" when it's obvious from the last few messages.

## Location Awareness
When {USER_NAME} says they are in a city ("I'm in Miami", "I'm traveling to NYC", "just landed in London"), ALWAYS call get_weather with that location. This updates the saved location so all future weather requests and morning briefings use the right city.

## Meeting Notes
When {USER_NAME} sends an audio file (via document upload or long voice note), it may be a meeting recording.
If the file path is provided and it looks like a meeting recording, use the process_meeting tool with the file path.
After processing, present the notes and ask the clarifying questions.
When {USER_NAME} answers clarification questions, use meeting_clarify with their answers.
You can retrieve past meeting notes with get_meeting_notes.
Meetings can be in any language the transcriber supports — it auto-detects.
IMPORTANT: process_meeting can take 1-2 minutes. Before calling it, tell {USER_NAME} you're starting (e.g. "Processing your meeting recording now — transcribing and analyzing. This may take a minute or two.")."""


def _prompt_communication_rules() -> str:
    """Communication style rules."""
    return f"""
## Voice
You can send voice messages using the send_audio tool. Use it when it feels natural — like when {USER_NAME} sends a voice note and a voice reply fits, when your response is long and better as audio, or when they ask for it. When you use send_audio, still include your text response as usual — the audio is a bonus, not a replacement. Write the audio text conversationally — no markdown, no bullets, just natural speech.

## Communication
ALWAYS acknowledge what you're doing. If you're processing something that takes time, tell {USER_NAME} immediately.
Never go silent — if something fails, say so. If something is taking long, say so."""


def _build_system_prompt(memory_context: str | None = None) -> str:
    now = datetime.now(TZ)

    prompt = _prompt_header(now)
    prompt += _load_personality()
    prompt += _prompt_behavior_rules()

    # Module info
    module_names = modules.get_module_names()
    if module_names:
        prompt += f"\n\n## Active Modules\n{', '.join(module_names)}"

    prompt += _prompt_communication_rules()

    if memory_context and memory_context != "No relevant memories found.":
        prompt += f"\n\n## Relevant Memories\n{memory_context}"

    return prompt


async def _execute_tool(name: str, input_data: dict, chat_id: int) -> str | dict:
    """Dispatch a tool call to the right implementation."""
    assert isinstance(name, str) and name, f"Tool name must be non-empty string, got: {name!r}"
    try:
        match name:
            case "remember":
                return await tools.remember(input_data["text"], input_data.get("tags"))
            case "recall":
                return await tools.recall(input_data["query"])
            case "get_calendar":
                return await tools.get_calendar(
                    input_data.get("date"), input_data.get("days", 1),
                    input_data.get("account"),
                )
            case "create_event":
                return await tools.create_event(
                    input_data["title"],
                    input_data["start"],
                    input_data.get("end"),
                    input_data.get("description"),
                    input_data.get("account", "personal"),
                    input_data.get("attendees"),
                )
            case "add_todo":
                return await tools.add_todo(
                    input_data["task"],
                    input_data.get("priority", "medium"),
                    input_data.get("due_date"),
                    input_data.get("project"),
                )
            case "list_todos":
                return await tools.list_todos(
                    input_data.get("project"),
                    input_data.get("status", "pending"),
                    input_data.get("priority"),
                )
            case "complete_todo":
                return await tools.complete_todo(input_data["todo_id"])
            case "delete_todo":
                return await tools.delete_todo(input_data["todo_id"])
            case "list_emails":
                return await tools.list_emails(
                    input_data.get("query", "is:inbox"),
                    input_data.get("max_results", 10),
                    input_data.get("account"),
                )
            case "read_email":
                return await tools.read_email(input_data["email_id"])
            case "send_email":
                return await tools.send_email(
                    input_data["to"], input_data["subject"], input_data["body"],
                    input_data.get("account", "personal"),
                )
            case "reply_email":
                return await tools.reply_email(input_data["email_id"], input_data["body"])
            case "set_reminder":
                return await tools.set_reminder(
                    input_data["text"], input_data["when"], chat_id
                )
            case "search_whatsapp":
                return await tools.search_whatsapp(
                    input_data["query"],
                    input_data.get("chat_name"),
                    input_data.get("hours_back", 24),
                )
            case "get_whatsapp_summary":
                return await tools.get_whatsapp_summary(
                    input_data.get("chat_name"),
                    input_data.get("hours_back", 24),
                )
            case "send_audio":
                return await tools.generate_audio(input_data["text"])
            case _:
                # Try module dispatch
                module_result = await modules.execute_tool(name, input_data)
                if module_result is not None:
                    return module_result
                return f"Unknown tool: {name}"
    except Exception as e:
        logger.exception(f"Tool {name} failed")
        return f"Tool error ({name}): {e}"


async def _call_claude_with_retry(selected_model: str, system: str, tool_defs: list[dict], messages: list[dict]) -> tuple[object | None, str | None]:
    """Call Claude API with exponential backoff retry.
    Returns (response, error_message). One of the two will be None.
    Uses beta API with compaction, adaptive thinking, and prompt caching.
    """
    for attempt in range(4):
        try:
            response = await client.beta.messages.create(
                betas=["compact-2026-01-12"],
                model=selected_model,
                max_tokens=CLAUDE_MAX_TOKENS,
                thinking={"type": "adaptive"},
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=tool_defs,
                messages=messages,
                context_management={"edits": [{"type": "compact_20260112"}]},
            )
            logger.info(f"Claude API responded: stop_reason={response.stop_reason}, blocks={len(response.content)}, model={selected_model}")
            return response, None
        except anthropic.APIStatusError as e:
            if e.status_code in (429, 529):
                if attempt < 3:
                    wait = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(f"API {e.status_code} (attempt {attempt + 1}/4), retrying in {wait}s")
                    await asyncio.sleep(wait)
                continue
            logger.exception(f"Claude API call failed: {e}")
            return None, f"Sorry, I hit an API error: {e}"
        except Exception as e:
            logger.exception(f"Claude API call failed: {e}")
            return None, f"Sorry, I hit an API error: {e}"
    # All retry attempts exhausted without break
    return None, "Sorry, the API is overloaded right now. Try again in a minute."


async def handle_message(chat_id: int, user_content: str | list, force_opus: bool = False) -> tuple[str, list[str]]:
    """Process a user message through the Claude agent loop.

    user_content can be a plain string or a list of content blocks
    (e.g. [{"type": "image", ...}, {"type": "text", ...}]) for multimodal input.

    Returns (text_response, audio_file_paths).
    """
    assert isinstance(chat_id, int) and chat_id != 0, f"Invalid chat_id: {chat_id}"
    assert isinstance(user_content, (str, list)), f"user_content must be str or list, got {type(user_content)}"

    # HANG-1 FIX: timeout on lock acquisition so messages don't queue forever
    lock = _get_chat_lock(chat_id)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=LOCK_ACQUIRE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return "I'm still working on something — give me a moment and try again.", []
    try:
        result = await _handle_message_locked(chat_id, user_content, force_opus)
        _evict_stale_conversations()
        return result
    finally:
        lock.release()


async def _handle_message_locked(chat_id: int, user_content: str | list, force_opus: bool = False) -> tuple[str, list[str]]:
    """Internal message handler — runs while holding the chat lock."""

    # Get or create conversation history
    if chat_id not in conversations:
        conversations[chat_id] = []
    messages = conversations[chat_id]

    # Auto-recall relevant memories for context
    recall_query = user_content if isinstance(user_content, str) else next(
        (b["text"] for b in user_content if b.get("type") == "text"), ""
    )
    logger.info(f"handle_message called (chat_id={chat_id}, query_len={len(recall_query) if recall_query else 0})")
    try:
        logger.info("Recalling memories...")
        memory_context = await asyncio.wait_for(
            tools.recall(recall_query), timeout=MEMORY_RECALL_TIMEOUT_SECONDS
        ) if recall_query else None
        logger.info(f"Memory recall done (found={memory_context is not None})")
    except asyncio.TimeoutError:
        logger.warning(f"Memory recall timed out ({MEMORY_RECALL_TIMEOUT_SECONDS}s) — skipping")
        memory_context = None
    except Exception:
        logger.exception("Memory recall failed")
        memory_context = None

    system = _build_system_prompt(memory_context)

    # Select model: force_opus for single message, persistent override, or default
    if force_opus:
        selected_model = MODEL_OPUS
    else:
        selected_model = model_overrides.get(chat_id, MODEL)

    # Store last real user message (before it gets trimmed) for context lookups
    if isinstance(user_content, str):
        _last_user_message[chat_id] = user_content
    elif isinstance(user_content, list):
        _last_user_message[chat_id] = " ".join(
            b.get("text", "") for b in user_content if isinstance(b, dict) and b.get("type") == "text"
        )

    # Verify history integrity after trim — first message must be "user" role
    if messages:
        assert messages[0].get("role") == "user", \
            f"History corruption: first message role is {messages[0].get('role')!r}, expected 'user'"

    # Add user message
    messages.append({"role": "user", "content": user_content})

    # Trim history to avoid context overflow — always trim at pair boundaries
    # to avoid splitting tool_use/tool_result pairs (causes API 400)
    _trim_history(messages)

    # Collect audio files from send_audio tool calls
    audio_files: list[str] = []

    # Agent loop — keeps running until Claude responds without tool calls
    max_iterations = 25
    for iteration in range(max_iterations):
        logger.info(f"Calling Claude API (iteration {iteration + 1}, model={selected_model}, msgs={len(messages)})")
        response, err = await _call_claude_with_retry(selected_model, system, _get_all_tool_definitions(), messages)
        if err is not None:
            return err, audio_files

        # Serialize content blocks for history (preserves compaction blocks)
        content = [block.model_dump() for block in response.content]
        messages.append({"role": "assistant", "content": content})

        # Server-side tool pause — re-send to continue (no user message needed)
        if response.stop_reason == "pause_turn":
            continue

        # Find user-defined tool use blocks (server_tool_use blocks are handled server-side)
        tool_uses = [b for b in response.content if b.type == "tool_use"]

        if not tool_uses:
            # No tool calls — extract text and return
            text_parts = [b.text for b in response.content if b.type == "text"]
            return "\n".join(text_parts) or "(no response)", audio_files

        # Execute all tool calls
        tool_results = []
        for tu in tool_uses:
            logger.info(f"Tool call: {tu.name}({tu.input})")
            result = await _execute_tool(tu.name, tu.input, chat_id)

            # Special handling for send_audio — collect file path as side-channel
            if tu.name == "send_audio" and isinstance(result, dict) and "audio_file" in result:
                audio_files.append(result["audio_file"])
                tool_result_str = "Audio message queued for delivery."
            else:
                tool_result_str = str(result)
                # Cap oversized tool results to prevent them from flooding
                # context and drowning out earlier conversation messages
                if len(tool_result_str) > TOOL_RESULT_MAX_CHARS:
                    tool_result_str = tool_result_str[:TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": tool_result_str,
                }
            )

        messages.append({"role": "user", "content": tool_results})

        # Trim inside tool loop to prevent context blow-up
        _trim_history(messages)

    return "I got stuck in a loop. Try rephrasing your request.", audio_files
