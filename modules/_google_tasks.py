"""Optional Google Tasks storage. SQLite stays the default for existing installations."""
import asyncio
import threading
from datetime import datetime

import tools
from constants import GOOGLE_PAGE_SIZE, TASK_NOTES_MAX_CHARS, TASK_WRITE_TIMEOUT_SECONDS

_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
# Worker threads serialize source lookup + write so simultaneous triage cannot duplicate tasks.
_task_write_lock = threading.Lock()


def _task_priority(t: dict) -> str:
    notes = (t.get("notes") or "").lower()
    for p in ("high", "low"):
        if f"priority: {p}" in notes:
            return p
    return "medium"


def _tasklists(service) -> list[dict]:
    return tools._list_google_pages(service.tasklists().list, {"maxResults": GOOGLE_PAGE_SIZE})


def _find_or_create_list(service, name: str, create: bool = True) -> dict | None:
    for tl in _tasklists(service):
        if tl["title"].lower() == name.lower():
            return tl
    if not create:
        return None
    return service.tasklists().insert(body={"title": name}).execute()


def _format_task(t: dict, list_title: str) -> str:
    from modules import feedback
    if t.get("_list_id"):
        ref = f"task:{t['_list_id']}:{t['id']}"
        source = next((line.removeprefix("Source-ID: ") for line in (t.get("notes") or "").splitlines()
                       if line.startswith("Source-ID: ")), "")
        feedback.register_item(ref, t.get("title", "(untitled)"), source)
        if feedback.should_suppress(ref, t.get("title", "")):
            return ""
    icon = "done" if t.get("status") == "completed" else "o"
    line = f"  [{icon}] #{t['id']} [{_task_priority(t)}] {t.get('title', '(untitled)')}"
    line += f" @{list_title}"
    if t.get("_list_id"):
        line += f" [ref: task:{t['_list_id']}:{t['id']}]"
    if t.get("due"):
        line += f" (due: {t['due'][:10]})"
    if t.get("notes"):
        line += f"\n    Notes: {t['notes'][:TASK_NOTES_MAX_CHARS]}"
    return line


def _find_task_source(service, source_id: str):
    """Source identity survives title changes and moves between lists."""
    assert source_id and "\n" not in source_id
    marker = f"Source-ID: {source_id}"
    for tasklist in _tasklists(service):
        tasks = tools._list_google_pages(service.tasks().list, {
            "tasklist": tasklist["id"], "maxResults": GOOGLE_PAGE_SIZE,
            "showCompleted": True, "showHidden": True,
        })
        for task in tasks:
            if marker in (task.get("notes") or "").splitlines():
                return tasklist, task
    return None, None


def _save_task(service, tasklist: dict, body: dict, source_id: str | None) -> str:
    assert isinstance(body, dict) and body.get("title"), "task title required"
    if source_id:
        old_list, existing = _find_task_source(service, source_id)
        if existing:
            if existing.get("status") == "completed":
                return f"Todo already tracked and completed: #{existing['id']} (Source-ID: {source_id})."
            if body.get("notes") == f"Source-ID: {source_id}":
                body["notes"] = existing.get("notes", body["notes"])
            # Keep a due date unless a replacement was explicitly provided.
            saved = service.tasks().patch(tasklist=old_list["id"], task=existing["id"], body=body).execute()
            assert saved.get("id"), "Google Tasks update returned no ID"
            return f"Todo updated in '{old_list['title']}': {body['title']} (#{saved['id']})"
    saved = service.tasks().insert(tasklist=tasklist["id"], body=body).execute()
    assert saved.get("id"), "Google Tasks insert returned no ID"
    return f"Todo added to '{tasklist['title']}': {body['title']} (#{saved['id']})"


async def add_todo(
    task: str,
    priority: str = "medium",
    due_date: str | None = None,
    project: str | None = None,
    notes: str | None = None,
    source_id: str | None = None,
) -> str:
    assert isinstance(task, str) and task.strip(), "task is required"
    if priority not in _PRIORITY_RANK:
        raise ValueError("priority must be high, medium, or low")
    if due_date:
        datetime.strptime(due_date, "%Y-%m-%d")
    if source_id and ("\n" in source_id or "\r" in source_id):
        raise ValueError("source_id must be one line")
    if source_id:
        from modules import feedback
        if feedback.is_suppressed(source_id, task):
            return f"Task suppressed by the user's saved decision: {source_id}. Only the user can reopen it."
    def _add():
        service = tools._google_service("personal", "tasks", "v1")
        if not service:
            return "Google Tasks unavailable. Set TODO_BACKEND=google and authorize the personal account with setup_calendar.py."
        body: dict = {"title": task}
        # Priority line first so _task_priority keeps parsing it; free-form notes after.
        note_lines = []
        if priority and priority != "medium":
            note_lines.append(f"Priority: {priority}")
        if notes:
            note_lines.append(notes)
        if source_id:
            note_lines.insert(0, f"Source-ID: {source_id}")
        if note_lines:
            body["notes"] = "\n".join(note_lines)
        if due_date:
            body["due"] = f"{due_date}T00:00:00.000Z"
        if not _task_write_lock.acquire(timeout=TASK_WRITE_TIMEOUT_SECONDS):
            raise TimeoutError("Task update is busy; no task was created.")
        try:
            tl = (_find_or_create_list(service, project) if project
                  else service.tasklists().get(tasklist="@default").execute())
            result = _save_task(service, tl, body, source_id)
        finally:
            _task_write_lock.release()
        parts = [result]
        if priority != "medium":
            parts.append(f"[{priority}]")
        if due_date:
            parts.append(f"(due: {due_date})")
        return " ".join(parts)

    return await asyncio.to_thread(_add)


async def list_todos(
    project: str | None = None,
    status: str = "pending",
    priority: str | None = None,
) -> str:
    def _list():
        service = tools._google_service("personal", "tasks", "v1")
        if not service:
            return "Google Tasks unavailable. Set TODO_BACKEND=google and authorize the personal account with setup_calendar.py."

        if project:
            tl = _find_or_create_list(service, project, create=False)
            if not tl:
                return f"No task list named '{project}'."
            lists = [tl]
        else:
            lists = _tasklists(service)

        include_done = status in ("done", "all")
        entries = []
        for tl in lists:
            tasks = tools._list_google_pages(service.tasks().list, {
                "tasklist": tl["id"], "maxResults": GOOGLE_PAGE_SIZE,
                "showCompleted": include_done, "showHidden": include_done,
            })
            for t in tasks:
                done = t.get("status") == "completed"
                if status == "pending" and done:
                    continue
                if status == "done" and not done:
                    continue
                if priority and _task_priority(t) != priority:
                    continue
                t = {**t, "_list_id": tl["id"]}
                entries.append((t, tl["title"]))

        if not entries:
            return "No todos found."

        entries.sort(key=lambda e: (_PRIORITY_RANK[_task_priority(e[0])], e[0].get("due", "9999")))
        lines = [_format_task(t, title) for t, title in entries]
        return "\n".join(line for line in lines if line) or "No todos to surface."

    return await asyncio.to_thread(_list)


def _find_task(service, task_id: str):
    """Locate a task by id across all lists. Returns (tasklist, task) or (None, None)."""
    from googleapiclient.errors import HttpError

    # ponytail: linear probe per list; fine at a handful of lists
    for tl in _tasklists(service):
        try:
            t = service.tasks().get(tasklist=tl["id"], task=task_id).execute()
            return tl, t
        except HttpError as e:
            if e.resp.status in (404, 400):
                continue
            raise
    return None, None


async def complete_todo(todo_id: str) -> str:
    def _do():
        service = tools._google_service("personal", "tasks", "v1")
        if not service:
            return "Google Tasks unavailable. Set TODO_BACKEND=google and authorize the personal account with setup_calendar.py."
        tl, t = _find_task(service, str(todo_id))
        if not t:
            return f"Todo #{todo_id} not found."
        if t.get("status") == "completed":
            return f"Todo #{todo_id} already done."
        service.tasks().patch(
            tasklist=tl["id"], task=t["id"], body={"status": "completed"}
        ).execute()
        return f"Todo completed: {t.get('title', todo_id)}"

    return await asyncio.to_thread(_do)


async def delete_todo(todo_id: str) -> str:
    def _do():
        service = tools._google_service("personal", "tasks", "v1")
        if not service:
            return "Google Tasks unavailable. Set TODO_BACKEND=google and authorize the personal account with setup_calendar.py."
        tl, t = _find_task(service, str(todo_id))
        if not t:
            return f"Todo #{todo_id} not found."
        service.tasks().delete(tasklist=tl["id"], task=t["id"]).execute()
        return f"Todo deleted: {t.get('title', todo_id)}"

    return await asyncio.to_thread(_do)
