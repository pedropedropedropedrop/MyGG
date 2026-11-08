"""Edit existing Google Tasks while preserving the source used for deduplication."""
import asyncio
from datetime import datetime

import tools
from modules import _google_tasks as google
from constants import TASK_WRITE_TIMEOUT_SECONDS


TOOL_DEFINITIONS = [{
    "name": "update_todo",
    "description": "Update an existing task without making a duplicate. Change title, priority, due date, notes, or status; source identity is preserved. Use status pending to explicitly reopen a completed task.",
    "input_schema": {
        "type": "object",
        "properties": {
            "todo_id": {"type": "string", "description": "Task ID from list_todos."},
            "task": {"type": "string", "description": "Replacement title."},
            "priority": {"type": "string", "enum": ["high", "medium", "low"]},
            "due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD, or null to clear the due date."},
            "notes": {"type": "string", "description": "Replacement context; the existing source ID is preserved."},
            "status": {"type": "string", "enum": ["pending", "done"]},
        },
        "required": ["todo_id"],
    },
}]


def _build_update(data: dict, existing: dict) -> dict:
    assert isinstance(data, dict) and isinstance(existing, dict)
    body = {}
    if "task" in data:
        if not isinstance(data["task"], str) or not data["task"].strip():
            raise ValueError("task must be non-empty text")
        body["title"] = data["task"]
    if "due_date" in data:
        date = data["due_date"]
        if date is not None:
            datetime.strptime(date, "%Y-%m-%d")
        body["due"] = f"{date}T00:00:00.000Z" if date else None
    if "status" in data:
        if data["status"] not in ("pending", "done"):
            raise ValueError("status must be pending or done")
        body["status"] = "completed" if data["status"] == "done" else "needsAction"
    if "priority" in data or "notes" in data:
        priority = data.get("priority", google._task_priority(existing))
        if priority not in google._PRIORITY_RANK:
            raise ValueError("priority must be high, medium, or low")
        source = [line for line in (existing.get("notes") or "").splitlines() if line.startswith("Source-ID: ")]
        notes = data.get("notes", existing.get("notes") or "")
        if not isinstance(notes, str):
            raise ValueError("notes must be text")
        content = [line for line in notes.splitlines() if not line.startswith(("Source-ID: ", "Priority: "))]
        body["notes"] = "\n".join(source + [f"Priority: {priority}"] + content)
    if not body:
        raise ValueError("provide at least one field to update")
    return body


def _update_task(data: dict) -> str:
    assert isinstance(data.get("todo_id"), str) and data["todo_id"].strip(), "todo_id is required"
    service = tools._google_service("personal", "tasks", "v1")
    if not service:
        return "Google Tasks unavailable (personal account auth failed)."
    if not google._task_write_lock.acquire(timeout=TASK_WRITE_TIMEOUT_SECONDS):
        raise TimeoutError("Task update is busy; nothing changed.")
    try:
        tasklist, existing = google._find_task(service, data["todo_id"])
        if not existing:
            return f"Todo #{data['todo_id']} not found."
        result = service.tasks().patch(tasklist=tasklist["id"], task=existing["id"], body=_build_update(data, existing)).execute()
        assert result.get("id"), "Google Tasks update returned no ID"
        return f"Todo updated: #{result['id']}"
    finally:
        google._task_write_lock.release()


def _update_local(data: dict) -> str:
    edits = {field: data[field] for field in ("task", "priority", "due_date", "notes", "status") if field in data}
    if not edits:
        raise ValueError("Provide at least one field to update")
    _build_update(edits, {})  # Apply the same title, date, priority and status rules.
    conn = tools.get_db()
    try:
        columns = ", ".join(f"{field}=?" for field in edits)
        changed = conn.execute(f"UPDATE todos SET {columns} WHERE id=?", (*edits.values(), data["todo_id"])).rowcount
        conn.commit()
        return f"Todo updated: #{data['todo_id']}" if changed else "Todo not found."
    finally:
        conn.close()


async def execute_tool(name: str, input_data: dict) -> str | None:
    assert isinstance(name, str) and isinstance(input_data, dict)
    if name != "update_todo":
        return None
    if tools.TODO_BACKEND == "google":
        return await asyncio.to_thread(_update_task, input_data)
    return await asyncio.to_thread(_update_local, input_data)
