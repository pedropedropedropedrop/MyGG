import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import agent
import heartbeat
import main
import tools
from modules import feedback, goals, protagonism, tasks


@pytest.fixture(autouse=True)
def private_store(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "DB_PATH", tmp_path / "assistant.db")
    monkeypatch.setattr(tools, "TODO_BACKEND", "sqlite")
    monkeypatch.setenv("ENABLE_MEMORY", "false")
    tools.init_db()


def test_plans_keep_prior_versions_and_respect_their_dates():
    goals.save_goals({"life_direction": "Make more time to read", "month": "2030-04",
                      "monthly_outcomes": [{"outcome": "Read two books", "measure": "Books completed"}]})
    goals.save_goals({"week_of": "2030-04-01", "weekly_priorities": [{"outcome": "Visit library"}]})
    assert goals.get_goal_history(1)[0]["goals"]["monthly_outcomes"][0]["measure"] == "Books completed"
    assert "Visit library" in goals.build_goal_context(datetime(2030, 4, 3))
    assert "Visit library" not in goals.build_goal_context(datetime(2030, 6, 3))
    with pytest.raises(ValueError):
        goals.save_goals({"week_of": "2030-06-03"})


def test_source_tasks_survive_renames_and_dismissals():
    async def run():
        ref = "gmail:work:synthetic-thread"
        await tools.add_todo("Reply about shipping", source_id=ref, notes="Check the address.")
        await tools.add_todo("Reply about revised shipping", source_id=ref)
        assert "Check the address" in await tools.list_todos()
        assert "updated" in await tasks.execute_tool("update_todo", {"todo_id": 1, "priority": "high"})
        feedback.record_notice(25, 4, "task:local:1", "Follow up on shipping")
        assert feedback.apply_reply_feedback(25, "stop reminding me", SimpleNamespace(message_id=4))
        with feedback.proactive_scope():
            assert "shipping" not in await tools.list_todos()
        assert "[high]" in await tools.list_todos()
        assert "dismissed" in await tools.add_todo("One more follow-up", source_id=ref)
        feedback.record_feedback(ref, "active")
        await tools.complete_todo(1)
        assert "already completed" in await tools.add_todo("Try again", source_id=ref)
        assert "updated" in await tasks.execute_tool("update_todo", {"todo_id": "1", "status": "pending"})
        assert (await tools.list_todos()).count("#1") == 1
    asyncio.run(run())


def test_board_and_knowledge_are_independently_configured(monkeypatch, tmp_path):
    board = tmp_path / "board"
    board.mkdir()
    (board / "task-1.md").write_text("---\ntitle: Home repair\nstatus: Active\n---\n- [ ] Fix the handle\n")
    monkeypatch.setattr(protagonism, "BOARD_DIR", board)
    text = protagonism._read_board()
    assert "Fix the handle" in text and "[ref:board:" in text
    ref = text.split("[ref:")[1].split("]")[0]
    feedback.record_feedback(ref, "dismissed")
    with feedback.proactive_scope():
        assert "Fix the handle" not in protagonism._read_board()
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "handbook.md").write_text("The wheel needs a six-millimeter bearing.")
    monkeypatch.setenv("KNOWLEDGE_DIR", str(notes))
    snippets = asyncio.run(protagonism.execute_tool("search_knowledge_base", {"question": "bearing", "top_k": 1}))
    assert "wheel" in snippets and "handbook.md" in snippets


def test_snoozed_followup_waits_and_respects_revoked_owner(monkeypatch):
    monkeypatch.setattr(protagonism, "OWNER_CHAT_ID", 25)
    monkeypatch.setattr(heartbeat, "OWNER_CHAT_ID", 25)
    monkeypatch.setattr(main, "ALLOWED_USER_IDS", {25})
    compose = AsyncMock(return_value=True)
    monkeypatch.setattr(protagonism, "compose_followup", compose)
    async def run():
        ref = feedback.register_item("task:local:8", "Library books")
        await protagonism.execute_tool("schedule_followup", {"topic": "books", "when": "2000-01-01T12:00", "item_ref": ref})
        feedback.record_feedback(ref, "snoozed", {"until": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()})
        await main.check_reminders(None)
        assert len(await tools.get_due_reminders()) == 1 and not compose.called
        feedback.record_feedback(ref, "active")
        await main.check_reminders(None)
        assert compose.await_count == 1 and not await tools.get_due_reminders()
        await protagonism.execute_tool("schedule_followup", {"topic": "books", "when": "2000-01-01T12:00"})
        monkeypatch.setattr(heartbeat, "OWNER_CHAT_ID", 26)
        await main.check_reminders(None)
        assert compose.await_count == 1
    asyncio.run(run())


def test_restart_history_omits_media_and_thinking():
    history = [
        {"role": "user", "content": [{"type": "document", "source": {"data": "raw-attachment"}},
                                      {"type": "text", "text": "Find the due date"}]},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "hidden reasoning"},
                                          {"type": "tool_use", "id": "r", "name": "list_todos", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "r", "content": "Tomorrow"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "It is due tomorrow."}]},
    ]
    assert agent._save_conversation(99, history)
    saved = json.dumps(agent._load_conversation(99))
    assert "raw-attachment" not in saved and "hidden reasoning" not in saved
    assert "Find the due date" in saved and "tool_result" in saved and "It is due tomorrow" in saved
    assert agent._load_conversation(98) == []


def test_google_tasks_edits_keep_source_and_list_identity(monkeypatch):
    monkeypatch.setattr(tools, "TODO_BACKEND", "google")
    service = Mock()
    monkeypatch.setattr(tools, "_google_service", lambda *args: service)
    service.tasklists().list().execute.return_value = {"items": [{"id": "list1", "title": "Work"}]}
    service.tasks().get().execute.return_value = {"id": "remote-1", "notes": "Source-ID: example\nPriority: high"}
    service.tasks().patch().execute.return_value = {"id": "remote-1"}
    updated = asyncio.run(tasks.execute_tool("update_todo", {"todo_id": "remote-1", "notes": "new details"}))
    assert "updated" in updated
    args = service.tasks().patch.call_args.kwargs
    assert args["tasklist"] == "list1" and args["task"] == "remote-1"
    assert args["body"]["notes"] == "Source-ID: example\nPriority: high\nnew details"


def test_background_research_blocks_unlisted_tools_and_keeps_user_history(monkeypatch):
    asyncio.run(tools.add_todo("Keep me"))
    history = [{"role": "user", "content": "Only in my conversation"}]
    monkeypatch.setattr(agent, "conversations", {7: history})
    calls = []
    def block(**fields):
        return SimpleNamespace(**fields, model_dump=lambda **_: dict(fields))

    async def model(model_name, system, definitions, messages, *args, **kwargs):
        calls.append((definitions, messages.copy()))
        if len(messages) == 1:
            content = [block(type="tool_use", id="x", name="delete_todo", input={"todo_id": 1})]
            return SimpleNamespace(content=content, stop_reason="tool_use"), None
        return SimpleNamespace(content=[block(type="text", text="Checked")], stop_reason="end_turn"), None
    monkeypatch.setattr(agent, "_call_model_with_retry", model)
    assert asyncio.run(agent.run_subagent("Inspect only", "Check", ["list_todos"])) == "Checked"
    assert [d["name"] for d in calls[0][0]] == ["list_todos"]
    assert "not allowed" in calls[-1][1][-1]["content"][0]["content"]
    calls.clear()
    assert asyncio.run(agent.handle_message(7, "Summarize", allowed_tools=[]))[0] == "Checked"
    assert calls[0][0] == [] and "unavailable" in calls[-1][1][-1]["content"][0]["content"].lower()
    assert agent.conversations[7] == history and "Keep me" in asyncio.run(tools.list_todos())


def test_scout_requires_a_registered_source_and_skips_dismissed_items(monkeypatch):
    monkeypatch.setattr(protagonism, "OWNER_CHAT_ID", 7)
    monkeypatch.setattr(protagonism, "_in_waking_window", lambda _: True)
    monkeypatch.setattr(protagonism, "_user_active_within", lambda _: False)
    monkeypatch.setattr(protagonism, "BOARD_DIR", None)
    scout = AsyncMock(return_value="TOPIC: new-delivery\nITEM_REF: task:local:9\nWHY_NOW: Due today")
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(agent, "run_subagent", scout)
    monkeypatch.setattr(protagonism, "_compose_and_send", send)
    asyncio.run(protagonism.scout_now(None))
    assert not send.called
    feedback.register_item("task:local:9", "Pick up delivery")
    asyncio.run(protagonism.scout_now(None))
    assert send.await_count == 1
    feedback.record_feedback("task:local:9", "dismissed")
    asyncio.run(protagonism.scout_now(None))
    assert send.await_count == 1
