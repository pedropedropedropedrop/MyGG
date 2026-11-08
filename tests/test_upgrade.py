import asyncio
import sqlite3
from types import SimpleNamespace

import agent
import tools


def test_legacy_todos_and_reminders_survive_repeated_upgrade(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "DB_PATH", tmp_path / "pa.db")
    monkeypatch.setattr(tools, "TODO_BACKEND", "sqlite")
    conn = sqlite3.connect(tools.DB_PATH)
    try:
        # Columns, defaults and primary keys from the original public SQLite schema.
        conn.executescript("""
            CREATE TABLE todos (id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT NOT NULL,
                priority TEXT DEFAULT 'medium', due_date TEXT, project TEXT, status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now')));
            CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, due_at TEXT NOT NULL,
                chat_id INTEGER NOT NULL, notified INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now')));
            INSERT INTO todos VALUES (8,'Pack bag','high','2030-01-02','Travel','pending','2025-01-02 09:00');
            INSERT INTO todos VALUES (15,'Fix lock','low',NULL,NULL,'done','2025-01-04 09:00');
            INSERT INTO reminders VALUES (5,'Pack today','2001-01-02T21:00:00',29,0,'2001-01-01');
            INSERT INTO reminders VALUES (6,'Previously delivered','2001-01-01T21:00:00',29,1,'2001-01-01');
        """)
        todo_cols = "id,task,priority,due_date,project,status,created_at"
        reminder_cols = "id,text,due_at,chat_id,notified,created_at"
        todos_before = conn.execute(f"SELECT {todo_cols} FROM todos ORDER BY id").fetchall()
        reminders_before = conn.execute(f"SELECT {reminder_cols} FROM reminders ORDER BY id").fetchall()
        tools.init_db()
        tools.init_db()
        assert conn.execute(f"SELECT {todo_cols} FROM todos ORDER BY id").fetchall() == todos_before
        assert conn.execute(f"SELECT {reminder_cols} FROM reminders ORDER BY id").fetchall() == reminders_before
        assert asyncio.run(tools.check_due_reminders()) == [(5, 29, "Pack today")]
        assert "#8" in asyncio.run(tools.list_todos()) and "#15" in asyncio.run(tools.list_todos(status="done"))
        asyncio.run(tools.add_todo("A new task", source_id="test:new-item", notes="Keep the source"))
        assert conn.execute("SELECT id,source_id,notes FROM todos WHERE id>15").fetchone() == (16, "test:new-item", "Keep the source")
        asyncio.run(tools.finish_reminder(5))
        assert asyncio.run(tools.check_due_reminders()) == []
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        conn.close()


def test_chat_resumes_through_normal_entry_after_reopening(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "DB_PATH", tmp_path / "pa.db")
    tools.init_db()
    monkeypatch.setenv("ENABLE_MEMORY", "false")
    monkeypatch.setattr(agent, "conversations", {})
    received = []
    async def fake_model(model, system, definitions, messages, *args, **kwargs):
        received.append(messages.copy())
        text = "The shelf is blue."
        block = SimpleNamespace(type="text", text=text, model_dump=lambda **kw: {"type": "text", "text": text})
        return SimpleNamespace(content=[block], stop_reason="end_turn"), None
    monkeypatch.setattr(agent, "_call_model_with_retry", fake_model)
    asyncio.run(agent.handle_message(179, "The shelf is blue."))
    agent.conversations.clear()
    tools.init_db()
    asyncio.run(agent.handle_message(179, "What color was the shelf?"))
    assert received[1][0] == {"role": "user", "content": "The shelf is blue."} or "The shelf is blue." in received[1][0]["content"]
    assert received[1][1]["role"] == "assistant"
    assert "What color was the shelf?" in received[1][-1]["content"]
