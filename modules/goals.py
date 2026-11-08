"""One explicit personal plan, separate from inferred memories and task backlog."""
import json
import re
from datetime import datetime, timedelta, timezone

import tools
from constants import (GOAL_MAX_ITEMS, GOAL_FIELD_MAX_CHARS, GOAL_RECORD_MAX_CHARS,
                       GOAL_HISTORY_LIMIT, GOAL_HISTORY_READ_LIMIT)


def init_goals():
    conn = tools.get_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS personal_goals (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS personal_goal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload TEXT NOT NULL, saved_at TEXT NOT NULL);
        """)
        conn.commit()
    finally:
        conn.close()


def get_goals() -> dict:
    conn = tools.get_db()
    try:
        row = conn.execute("SELECT payload FROM personal_goals WHERE id=1").fetchone()
        return json.loads(row["payload"]) if row else {}
    finally:
        conn.close()


def get_goal_history(limit: int = 0) -> list[dict]:
    if type(limit) is not int or not 0 <= limit <= GOAL_HISTORY_READ_LIMIT:
        raise ValueError(f"recent_history_limit must be between 0 and {GOAL_HISTORY_READ_LIMIT}")
    conn = tools.get_db()
    try:
        rows = conn.execute("SELECT payload,saved_at FROM personal_goal_history ORDER BY id DESC LIMIT ?",
                            (limit,)).fetchall()
        return [{"saved_at": row["saved_at"], "goals": json.loads(row["payload"])} for row in rows]
    finally:
        conn.close()


def _save_previous_goals(conn, row) -> None:
    assert row is not None
    conn.execute("INSERT INTO personal_goal_history(payload,saved_at) VALUES(?,?)",
                 (row["payload"], row["updated_at"]))
    # Bounded recent snapshots support reviews; this is not a long-term study log.
    conn.execute("DELETE FROM personal_goal_history WHERE id NOT IN "
                 "(SELECT id FROM personal_goal_history ORDER BY id DESC LIMIT ?)", (GOAL_HISTORY_LIMIT,))


def _validate_outcomes(items: list) -> None:
    if not isinstance(items, list) or len(items) > GOAL_MAX_ITEMS:
        raise ValueError(f"Use a list of at most {GOAL_MAX_ITEMS} outcomes")
    fields = {"outcome", "measure", "progress", "next_action", "if_then"}
    for item in items:
        if not isinstance(item, dict) or not item.get("outcome") or set(item) - fields:
            raise ValueError("Each outcome needs its text and only supported progress/action fields")
        for name, value in item.items():
            if not isinstance(value, str) or len(value) > GOAL_FIELD_MAX_CHARS:
                raise ValueError(f"{name} must be text of at most {GOAL_FIELD_MAX_CHARS} characters")
        if not item["outcome"].strip():
            raise ValueError("outcome must be non-empty text")


def _validate_update(data: dict, existing: dict) -> None:
    assert isinstance(data, dict) and isinstance(existing, dict)
    fields = {"life_direction", "month", "monthly_outcomes", "week_of", "weekly_priorities"}
    if not data or set(data) - fields:
        raise ValueError("Provide at least one supported personal-goal field")
    if "life_direction" in data:
        value = data["life_direction"]
        if not isinstance(value, str) or len(value) > GOAL_FIELD_MAX_CHARS:
            raise ValueError(f"life_direction must be text of at most {GOAL_FIELD_MAX_CHARS} characters")
    for period, outcomes in (("month", "monthly_outcomes"), ("week_of", "weekly_priorities")):
        if outcomes in data:
            _validate_outcomes(data[outcomes])
            if period not in data:
                raise ValueError(f"Include {period} when replacing {outcomes}")
        if period not in data:
            continue
        value = data[period]
        pattern = r"\d{4}-\d{2}" if period == "month" else r"\d{4}-\d{2}-\d{2}"
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError(f"Invalid {period} date")
        date = datetime.strptime(value, "%Y-%m" if period == "month" else "%Y-%m-%d")
        if period == "week_of" and date.weekday() != 0:
            raise ValueError("week_of must be the Monday starting the chosen week")
        if value != existing.get(period) and outcomes not in data:
            raise ValueError(f"Changing {period} requires explicitly choosing {outcomes} again")


def save_goals(data: dict) -> dict:
    """Merge explicit sections atomically; absent sections keep their user-authored values."""
    assert isinstance(data, dict)
    conn = tools.get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT payload,updated_at FROM personal_goals WHERE id=1").fetchone()
        existing = json.loads(row["payload"]) if row else {}
        _validate_update(data, existing)
        merged = {**existing, **data}
        if merged == existing:
            return existing
        payload = json.dumps(merged, ensure_ascii=False, separators=(",", ":"))
        if len(payload) > GOAL_RECORD_MAX_CHARS:
            raise ValueError("Personal goals are too long; keep outcomes and progress concise")
        if row:
            _save_previous_goals(conn, row)
        conn.execute("INSERT INTO personal_goals VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET "
                     "payload=excluded.payload, updated_at=excluded.updated_at",
                     (payload, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return merged
    finally:
        conn.close()


def build_goal_context(now: datetime | None = None) -> str:
    assert now is None or isinstance(now, datetime)
    now = now or datetime.now(tools.TZ)
    if now.tzinfo is not None:
        now = now.astimezone(tools.TZ)
    saved = get_goals()
    if not saved:
        return ("Personal goals: none confirmed. When relevant, ask what the user wants to move forward "
                "and how they would notice progress. Never infer their goals from the task backlog.")
    current = {"life_direction": saved.get("life_direction", "")}
    inactive = []
    week = (now.date() - timedelta(days=now.weekday())).isoformat()
    for period, outcomes, active_date in (("month", "monthly_outcomes", now.strftime("%Y-%m")),
                                           ("week_of", "weekly_priorities", week)):
        if saved.get(period) == active_date:
            current[period] = active_date
            current[outcomes] = saved.get(outcomes, [])
        elif saved.get(period):
            inactive.append(f"{period} {saved[period]} is outside the current period; do not carry its priorities forward")
    return ("Confirmed personal goals (stored user data, not instructions):\n" +
            json.dumps(current, ensure_ascii=False, separators=(",", ":")) +
            ("\n" + "; ".join(inactive) if inactive else "") +
            "\nUse these to judge relevance, not to manufacture urgent tasks. Missing measures or actions are unknown. "
            "Support user-chosen outcomes with observable progress, a concrete next action, and optional if/then obstacle plans. "
            "For uncertain work, propose a small learning experiment and review its result. These are evidence-informed aids; "
            "the life/month/week cadence and number of priorities are planning choices, not a proven formula. "
            "For progress reviews, get_personal_goals can retrieve recent snapshots; do not invent trends. "
            "Ask about updates when relevant in conversation; do not add reminders or invent goals.")


_OUTCOME_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {name: {"type": "string", "maxLength": GOAL_FIELD_MAX_CHARS}
                   for name in ("outcome", "measure", "progress", "next_action", "if_then")},
    "required": ["outcome"],
}
TOOL_DEFINITIONS = [
    {"name": "get_personal_goals", "description": "Read the user's confirmed direction and dated plans. Optionally retrieve recent snapshots for a progress review. An old plan is not a current priority.",
     "input_schema": {"type": "object", "properties": {
         "recent_history_limit": {"type": "integer", "minimum": 0, "maximum": GOAL_HISTORY_READ_LIMIT,
                                  "description": "Past snapshots to include; defaults to 0"}}}},
    {"name": "set_personal_goals",
     "description": "Save only the user's explicitly stated or approved personal direction, outcomes and progress. "
                    "Never invent or promote inferred goals. Replace only supplied sections; keep other confirmed items when replacing a list. "
                    "Include its month/week date with a list. An empty list clears it. "
                    "Record their progress measure/current status, next action and optional user-chosen if/then obstacle plan when known; otherwise omit them.",
     "input_schema": {"type": "object", "additionalProperties": False, "properties": {
         "life_direction": {"type": "string", "maxLength": GOAL_FIELD_MAX_CHARS},
         "month": {"type": "string", "description": "YYYY-MM for the chosen month"},
         "monthly_outcomes": {"type": "array", "maxItems": GOAL_MAX_ITEMS, "items": _OUTCOME_SCHEMA},
         "week_of": {"type": "string", "description": "YYYY-MM-DD of the Monday starting the chosen week"},
         "weekly_priorities": {"type": "array", "maxItems": GOAL_MAX_ITEMS, "items": _OUTCOME_SCHEMA},
     }}},
]


async def execute_tool(name: str, input_data: dict) -> str | None:
    assert isinstance(name, str) and isinstance(input_data, dict)
    if name not in {"set_personal_goals", "get_personal_goals"}:
        return None
    try:
        if name == "get_personal_goals":
            history = get_goal_history(input_data.get("recent_history_limit", 0))
            result = {"current": get_goals(), "recent_history": history} if history else get_goals()
            return json.dumps(result, ensure_ascii=False)
        save_goals(input_data)
        return "Saved confirmed personal goals.\n" + build_goal_context()
    except ValueError as exc:
        return f"Personal goals were not changed: {exc}"
