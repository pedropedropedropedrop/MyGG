import asyncio
import importlib.util
import json
from unittest.mock import AsyncMock, Mock

import httpx
from telegram import Update
from telegram.ext import Application
from telegram.request import BaseRequest

import agent
import llm
import main
import tools
import whatsapp_summarizer
from modules import meetings


def load_backend(monkeypatch, name):
    monkeypatch.setenv("LLM_BACKEND", name)
    monkeypatch.setenv("LOCAL_MODEL", "qwen3:8b")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://cloud.example.invalid")
    spec = importlib.util.spec_from_file_location("test_backend", llm.__file__)
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    return backend


def reply(content, stop="end_turn"):
    return httpx.Response(200, json={
        "type": "message", "role": "assistant", "id": "test", "model": "test",
        "stop_reason": stop, "content": content,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    })


def test_local_tool_roundtrip_and_media(monkeypatch, tmp_path):
    backend = load_backend(monkeypatch, "local")
    monkeypatch.setattr(agent, "llm", backend)
    monkeypatch.setenv("ENABLE_MEMORY", "false")
    monkeypatch.setattr(agent, "conversations", {})
    monkeypatch.setattr(tools, "DB_PATH", tmp_path / "test.db")
    tools.init_db()
    monkeypatch.setattr(agent, "model_overrides", {9: "some-cloud-model"})
    monkeypatch.setattr(tools, "list_todos", AsyncMock(return_value="Pick up milk"))
    requests = []

    def ollama(request):
        requests.append(request)
        if len(requests) == 1:
            return reply([{"type": "tool_use", "name": "list_todos",
                           "id": "test-call", "input": {}}], "tool_use")
        return reply([{"type": "text", "text": "Pick up milk."}])

    monkeypatch.setattr(backend.client, "_client", httpx.AsyncClient(transport=httpx.MockTransport(ollama)))
    assert asyncio.run(agent.handle_message(9, "Show my todos", force_opus=True)) == ("Pick up milk.", [])
    assert all(str(r.url) == "http://127.0.0.1:11434/v1/messages" for r in requests)
    assert all(r.headers["X-Api-Key"] == "ollama" for r in requests)
    first, second = [json.loads(r.content) for r in requests]
    assert first["model"] == "qwen3:8b"
    names = {t["name"] for t in first["tools"]}
    assert "list_todos" in names
    assert not names & {"web_search", "web_fetch", "code_execution", "send_audio", "recall", "remember"}
    assert second["messages"][-1]["content"] == [
        {"type": "tool_result", "tool_use_id": "test-call", "content": "Pick up milk"}
    ]
    media_reply, _ = asyncio.run(agent.handle_message(9, [{"type": "image"}]))
    assert "text" in media_reply
    assert "disabled" in asyncio.run(agent._execute_tool("send_audio", {"text": "private"}, 9))
    assert len(requests) == 2


def test_claude_chat_and_plain_messages(monkeypatch):
    backend = load_backend(monkeypatch, "anthropic")
    requests = []

    def claude(request):
        requests.append(request)
        return reply([{"type": "text", "text": "OK"}])

    monkeypatch.setattr(backend.client, "_client", httpx.AsyncClient(transport=httpx.MockTransport(claude)))
    messages = [{"role": "user", "content": "Hello"}]
    asyncio.run(backend.create(model="chosen-model", system="Assistant", tools=[], messages=messages,
                               max_tokens=10, container="test-container"))
    asyncio.run(backend.create(messages=messages, max_tokens=10))
    chat, summary = [json.loads(r.content) for r in requests]
    assert all(r.url.host == "cloud.example.invalid" for r in requests)
    assert chat["model"] == "chosen-model" and chat["container"] == "test-container"
    assert chat["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "context_management" in chat and "context_management" not in summary


def test_meeting_and_whatsapp_generation(monkeypatch, tmp_path):
    backend = load_backend(monkeypatch, "local")
    monkeypatch.setattr(meetings, "llm", backend)
    monkeypatch.setattr(whatsapp_summarizer, "llm", backend)
    monkeypatch.setattr(tools, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(tools, "remember", AsyncMock())
    monkeypatch.setenv("ENABLE_MEMORY", "true")
    tools.init_db()
    db = tools.get_db()
    try:
        for number in ("1", "2"):
            db.execute("""INSERT INTO whatsapp_messages
                (chat_jid, chat_name, sender_jid, whatsapp_message_id, message_text, timestamp)
                VALUES (?, 'Family', 'test', ?, ?, '2026-01-02T12:00:00')""",
                (number, number, "Message " + number))
        db.commit()
    finally:
        db.close()
    requests = []

    def ollama(request):
        requests.append(request)
        answer = '{"summary":"Discussed the contract."}' if len(requests) == 1 else "Bring lunch."
        return reply([{"type": "thinking", "thinking": "test", "signature": ""},
                      {"type": "text", "text": answer}])

    monkeypatch.setattr(backend.client, "_client", httpx.AsyncClient(transport=httpx.MockTransport(ollama)))
    notes = asyncio.run(meetings._process_with_claude("Meeting transcript", {}))
    assert notes["summary"] == "Discussed the contract."
    asyncio.run(whatsapp_summarizer.summarize_whatsapp())
    assert len(requests) == 3 and all(r.url.host == "127.0.0.1" for r in requests)
    assert tools.remember.await_count == 2
    assert all(call.kwargs["infer"] is False for call in tools.remember.await_args_list)
    assert [call.kwargs["tags"][-2:] for call in tools.remember.await_args_list] == [
        ["1", "2026-01-02"], ["2", "2026-01-02"],
    ]
    monkeypatch.setenv("ENABLE_MEMORY", "false")
    asyncio.run(whatsapp_summarizer.summarize_whatsapp())
    assert len(requests) == 3


def test_memory_receipts_and_unstored_whatsapp_batch(monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_MEMORY", "true")
    memory = Mock()
    memory.add.side_effect = [{"results": []}, {"results": [{"id": "saved", "event": "ADD"}]}]
    monkeypatch.setattr(tools, "_get_memory", lambda: memory)
    assert "Stored:" in asyncio.run(tools.remember("Example preference"))
    assert memory.add.call_args.kwargs["infer"] is False
    assert memory.add.call_args.kwargs["metadata"]["verbatim_fallback"] is True
    memory.add.side_effect = None
    memory.add.return_value = {"results": []}
    monkeypatch.setattr(tools, "DB_PATH", tmp_path / "store.db")
    tools.init_db()
    db = tools.get_db()
    try:
        db.execute("""INSERT INTO whatsapp_messages
            (chat_jid, sender_jid, whatsapp_message_id, message_text, timestamp)
            VALUES ('a', 'b', 'id', 'Meet tomorrow', '2026-03-01T18:00:00')""")
        db.commit()
        summarize = AsyncMock(return_value=Mock(content=[Mock(type="text", text="Meet tomorrow.")]))
        monkeypatch.setattr(whatsapp_summarizer.llm, "create", summarize)
        asyncio.run(whatsapp_summarizer.summarize_whatsapp())
        assert db.execute("SELECT summarized FROM whatsapp_messages").fetchone()[0] == 0
        assert memory.add.call_args.kwargs["infer"] is False
        memory.add.return_value = {"results": [{"id": "saved", "event": "ADD"}]}
        asyncio.run(whatsapp_summarizer.summarize_whatsapp())
        assert db.execute("SELECT summarized FROM whatsapp_messages").fetchone()[0] == 1
        asyncio.run(whatsapp_summarizer.summarize_whatsapp())
        assert summarize.await_count == 2
    finally:
        db.close()


def test_local_history_keeps_current_tool_turn(monkeypatch):
    monkeypatch.setattr(llm, "LOCAL", True)
    current = [{"role": "user", "content": "Most recent question"},
               {"role": "assistant", "content": [{"type": "tool_use", "id": "a"}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a",
                                              "content": "x" * 25000}]}]
    history = [{"role": "user", "content": "Old question"}] + current
    agent._trim_history(history)
    assert history == current


def test_first_telegram_chat_and_optional_voice(monkeypatch, tmp_path):
    backend = load_backend(monkeypatch, "local")
    monkeypatch.setattr(agent, "llm", backend)
    monkeypatch.setattr(main, "llm", backend)
    monkeypatch.setattr(main, "ALLOWED_USER_IDS", {7})
    monkeypatch.setattr(main.audio, "can_transcribe", lambda: False)
    monkeypatch.setattr(agent, "conversations", {})
    monkeypatch.setenv("ENABLE_MEMORY", "false")
    monkeypatch.setattr(tools, "DB_PATH", tmp_path / "bot.db")
    tools.init_db()
    replies = []
    chat = {"id": 7, "type": "private"}
    user = {"id": 7, "is_bot": False, "first_name": "Friend"}
    bot_user = {"id": 42, "is_bot": True, "first_name": "GG", "username": "test_gg_bot"}

    class TelegramAPI(BaseRequest):
        read_timeout = 1

        async def initialize(self):
            pass

        async def shutdown(self):
            pass

        async def do_request(self, url, method, request_data=None, **kwargs):
            endpoint = url.rsplit("/", 1)[-1]
            if endpoint == "getMe":
                result = bot_user
            elif endpoint == "sendMessage":
                text = request_data.parameters["text"]
                replies.append(text)
                result = {"message_id": 200, "date": 1, "chat": chat, "from": bot_user, "text": text}
            else:
                assert endpoint == "sendChatAction"
                result = True
            return 200, json.dumps({"ok": True, "result": result}).encode()

    def ollama(request):
        assert request.url.host == "127.0.0.1"
        history = json.loads(request.content)["messages"]
        if len(history) == 1:
            return reply([{"type": "tool_use", "id": "abc", "name": "add_todo",
                           "input": {"task": "Pack for Friday"}}], "tool_use")
        assert "Pack for Friday" in history[-1]["content"][0]["content"]
        return reply([{"type": "text", "text": "Added Pack for Friday."}])

    monkeypatch.setattr(backend.client, "_client", httpx.AsyncClient(transport=httpx.MockTransport(ollama)))

    async def chat_with_gg():
        app = Application.builder().token("42:test").request(TelegramAPI()).get_updates_request(TelegramAPI()).build()
        main._register_handlers(app)
        async with app:
            messages = [
                {"text": "/start", "entities": [{"type": "bot_command", "offset": 0, "length": 6}]},
                {"text": "Remember to pack for Friday."},
                {"voice": {"file_id": "audio-id", "file_unique_id": "file-uid", "duration": 8}},
            ]
            for i, data in enumerate(messages, start=1):
                incoming = {"update_id": i, "message": {"message_id": i, "date": 1, "chat": chat,
                                                        "from": user, **data}}
                await app.process_update(Update.de_json(incoming, app.bot))

    asyncio.run(chat_with_gg())
    assert "Your Telegram ID: 7" in replies[0]
    assert replies[1:] == ["Added Pack for Friday.", main.audio.VOICE_SETUP]
    assert "Pack for Friday" in asyncio.run(tools.list_todos())


def test_meeting_uses_extended_transcription_deadline(monkeypatch):
    transcribe = AsyncMock(return_value="Meeting decisions")
    monkeypatch.setattr(tools, "transcribe_audio", transcribe)
    assert asyncio.run(meetings._transcribe_audio("upload.wav"))[0] == "Meeting decisions"
    transcribe.assert_awaited_once_with("upload.wav", timeout=600)

def test_mixed_workloads_keep_web_and_vector_lookup_on_the_selected_backend(monkeypatch):
    from modules import _web, protagonism
    monkeypatch.setattr(llm, "LOCAL", False)
    monkeypatch.setenv("LLM_BACKEND_MAIN", "anthropic")
    monkeypatch.setenv("LLM_BACKEND_SUBAGENT", "local")
    monkeypatch.setenv("ENABLE_LOCAL_WEB", "true")
    monkeypatch.delenv("KNOWLEDGE_DIR", raising=False)
    monkeypatch.setenv("KNOWLEDGE_QDRANT_URL", "https://vector.example.invalid")
    monkeypatch.setenv("KNOWLEDGE_COLLECTION", "fixture")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-only")
    cloud_defs = {t["name"]: t for t in agent._get_all_tool_definitions("main")}
    local_defs = {t["name"]: t for t in agent._get_all_tool_definitions("subagent")}
    assert cloud_defs["web_search"]["type"].startswith("web_search_")
    assert "input_schema" in local_defs["web_search"]
    called = AsyncMock(return_value="Synthetic public search")
    monkeypatch.setattr(_web, "search", called)
    assert "hosted" in asyncio.run(agent._execute_tool("web_search", {"query": "synthetic"}, 7))
    called.assert_not_awaited()
    assert "Synthetic" in asyncio.run(agent._execute_tool("web_search", {"query": "synthetic"}, 7, caller="subagent"))
    monkeypatch.setattr(agent.modules, "_tool_dispatch", {"search_knowledge_base": protagonism})
    answer = asyncio.run(agent._execute_tool("search_knowledge_base", {"question": "private fixture"}, 7, caller="subagent"))
    assert "KNOWLEDGE_DIR" in answer
    assert llm.tool_caller.get() == "main"
