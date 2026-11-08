"""Named constants for GG — no magic numbers in source files.

All numeric and string literals that control behavior should be defined here
with a descriptive name explaining what they represent and why that value.
"""

# ── Claude API ──────────────────────────────────────────────────────────────

CLAUDE_MAX_TOKENS = 16000         # Max response tokens per Claude API call
TOOL_RESULT_MAX_CHARS = 6000      # Truncation limit for tool results sent back to Claude

# ── Email ───────────────────────────────────────────────────────────────────

EMAIL_DRAFT_MAX_CHARS = 4096      # Maximum email body to preview for approval
DRAFT_TTL_SECONDS = 600           # Telegram approval buttons expire after ten minutes
EMAIL_THREAD_MAX_MESSAGES = 10
EMAIL_MIME_PART_LIMIT = 100
GOOGLE_API_TIMEOUT_SECONDS = 30
EMAIL_BODY_MAX_CHARS = 3000       # Truncation limit for email body in read_email

# ── Web (removed — now using Anthropic server-side tools) ──────────────────

# ── Files ───────────────────────────────────────────────────────────────────

PDF_MAX_FILE_BYTES = 10_000_000
PDF_MAX_PAGES = 20
PDF_MAX_TEXT_CHARS = 16000
PDF_MAX_PAGE_CONTENT_BYTES = 2_000_000
PDF_EXTRACT_TIMEOUT_SECONDS = 30
FILE_READ_MAX_CHARS = 8000        # Max chars to read from uploaded text documents

# ── Audio / Meetings ────────────────────────────────────────────────────────

VOICE_LONG_THRESHOLD_SECONDS = 30 # Audio longer than this routes to meeting processing
MAX_TRANSCRIPT_CHARS = 150000     # Truncation limit for meeting transcripts sent to Claude
TRANSCRIBE_TIMEOUT_SECONDS = 60   # Timeout for Whisper transcription

# ── Conversation / Memory ───────────────────────────────────────────────────

MAX_HISTORY = 80                  # Max messages per chat (tool calls ~4 msgs each, so ~15-20 real turns)
MAX_CONVERSATIONS = 50            # Max concurrent chat histories before eviction

# ── Telegram ────────────────────────────────────────────────────────────────

TELEGRAM_MAX_LENGTH = 4000        # Telegram message character limit
TYPING_MAX_DURATION_SECONDS = 600 # Max time typing indicator runs (10 min safety cap)

# ── WhatsApp ────────────────────────────────────────────────────────────────

WA_SUMMARIZE_BATCH_LIMIT = 500    # Max messages to fetch per summarization batch

# ── Timeouts ────────────────────────────────────────────────────────────────

CLAUDE_API_TIMEOUT_SECONDS = 120  # Timeout for Claude API calls in meeting processing
CLAUDE_CLARIFY_TIMEOUT_SECONDS = 60  # Timeout for Claude API calls in clarification
MEMORY_RECALL_TIMEOUT_SECONDS = 15   # Timeout for mem0 recall operations
LOCK_ACQUIRE_TIMEOUT_SECONDS = 120   # Timeout for acquiring per-chat locks

# ── Whoop ───────────────────────────────────────────────────────────────────

WHOOP_API_TIMEOUT_SECONDS = 15           # Timeout for Whoop API HTTP calls
WHOOP_TOKEN_REFRESH_SKEW_SECONDS = 60    # Refresh if token expires within this window
WHOOP_MAX_DAYS_LOOKBACK = 7              # Hard cap on `days` parameter across Whoop tools
WHOOP_DEFAULT_DAYS = 1                   # Default `days` value when caller omits it
WHOOP_MAX_WORKOUTS = 10                  # Cap on workouts returned by get_whoop_workouts
WHOOP_DEFAULT_WORKOUTS = 3               # Default workouts returned
WHOOP_OAUTH_CALLBACK_PORT = 8788         # Localhost port the OAuth helper listens on
WHOOP_OAUTH_CALLBACK_TIMEOUT_SECONDS = 300  # Max wait for the user to approve consent
WHOOP_REFRESH_RETRY_LIMIT = 1            # Max token-refresh retries per HTTP request (NASA Rule 1)
WHOOP_OAUTH_AUTHZ_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
WHOOP_OAUTH_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
WHOOP_API_BASE = "https://api.prod.whoop.com/developer"
WHOOP_OAUTH_SCOPES = (
    "read:recovery read:sleep read:workout read:cycles "
    "read:profile read:body_measurement offline"
)

GOOGLE_PAGE_SIZE = 100
TASK_NOTES_MAX_CHARS = 500
TASK_WRITE_TIMEOUT_SECONDS = 30
GOAL_MAX_ITEMS = 5
GOAL_FIELD_MAX_CHARS = 400
GOAL_RECORD_MAX_CHARS = 5000
GOAL_HISTORY_LIMIT = 100
GOAL_HISTORY_READ_LIMIT = 10
FEEDBACK_MAX_ITEMS = 5000
FEEDBACK_CONTEXT_ITEMS = 30
FEEDBACK_TEXT_MAX_CHARS = 1000
FEEDBACK_REF_MAX_CHARS = 256
FEEDBACK_NOTICE_LIMIT = 2000
FEEDBACK_ALIAS_LIMIT = 30000
SUBAGENT_MAX_ITERATIONS = 6   # Hard cap on tool-use iterations per sub-agent invocation
SUBAGENT_CHAT_ID = -1         # Sentinel chat_id for sub-agents — chat-bound tools must reject this
PRE_MEETING_ALERT_WINDOW_MIN = 15  # Fire pre-meeting alert when event starts within this many minutes
MEETING_LOOKAHEAD_MIN = 20         # How far ahead to query the calendar each scheduler tick

GOOGLE_PAGE_LIMIT = 10
