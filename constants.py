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
EMAIL_BODY_MAX_CHARS = 3000       # Truncation limit for email body in read_email

# ── Web (removed — now using Anthropic server-side tools) ──────────────────

# ── Files ───────────────────────────────────────────────────────────────────

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
