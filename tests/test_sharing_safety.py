"""Sharing defaults and trusted outbound approval, with no network or accounts."""

import asyncio
import base64
from email import message_from_bytes
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import agent
import main
import tools
import whatsapp_server
from utils import validate_access


@pytest.fixture(autouse=True)
def isolate_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "DB_PATH", tmp_path / "assistant.db")


def update(user=7, chat=7, kind="private"):
    return SimpleNamespace(effective_user=SimpleNamespace(id=user),
                           effective_chat=SimpleNamespace(id=chat, type=kind),
                           message=AsyncMock(), callback_query=AsyncMock())


def test_access_requires_explicit_private_users(monkeypatch):
    monkeypatch.setenv("ALLOWED_USER_IDS", "")
    with pytest.raises(ValueError, match="ALLOWED_USER_IDS"):
        validate_access()
    monkeypatch.setenv("ALLOWED_USER_IDS", "7, 8")
    monkeypatch.setenv("OWNER_CHAT_ID", "-1")
    with pytest.raises(ValueError, match="OWNER_CHAT_ID"):
        validate_access()
    monkeypatch.setenv("OWNER_CHAT_ID", "")
    assert validate_access() == ({7, 8}, 0)
    monkeypatch.setattr(main, "ALLOWED_USER_IDS", {7})
    assert main._is_allowed(update())
    assert not main._is_allowed(update(user=8, chat=8))
    assert not main._is_allowed(update(chat=-1, kind="group"))


def test_bridge_requires_shared_secret(monkeypatch):
    request = SimpleNamespace(headers={}, json=AsyncMock())
    monkeypatch.setattr(whatsapp_server, "BRIDGE_SECRET", "")
    assert asyncio.run(whatsapp_server.start_server()) is None
    assert asyncio.run(whatsapp_server.handle_incoming(request)).status == 401
    request.json.assert_not_awaited()
    monkeypatch.setattr(whatsapp_server, "BRIDGE_SECRET", "test-only")
    assert not whatsapp_server._check_auth(request)
    request.headers["Authorization"] = "Bearer test-only"
    assert whatsapp_server._check_auth(request)


def test_reply_requires_click_and_sends_frozen_message_once(monkeypatch):
    service = MagicMock()
    users = service.users()
    users.getProfile.return_value.execute.return_value = {"emailAddress": "me@example.com"}
    messages = users.messages()
    messages.get.return_value.execute.return_value = {
        "threadId": "thread1", "payload": {"headers": [
            {"name": "From", "value": "sender@example.com"},
            {"name": "Reply-To", "value": "reply@example.com"},
            {"name": "Message-ID", "value": "<id@example.com>"},
            {"name": "Subject", "value": "Plans"},
        ]}}
    messages.send.return_value.execute.return_value = {"id": "receipt"}
    monkeypatch.setattr(tools, "_get_gmail_service", lambda account: service)
    monkeypatch.setattr(main, "ALLOWED_USER_IDS", {7})
    msg, context = update(), SimpleNamespace(chat_data={})
    async def present(channel, preview, payload):
        return await main._show_draft(msg, channel, preview, payload)

    args = {"email_id": "personal:msg1", "body": "See you soon."}
    assert "approval" in asyncio.run(agent._execute_tool("reply_email", args, 7))
    asyncio.run(agent._execute_tool("reply_email", args, 7, present))
    preview = msg.message.reply_text.await_args.args[0]
    assert "To: reply@example.com" in preview and "Re: Plans" in preview and args["body"] in preview
    messages.send.assert_not_called()
    keyboard = msg.message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
    msg.callback_query.data = keyboard[0][0].callback_data
    asyncio.run(main.handle_draft_approval(msg, context))
    asyncio.run(main.handle_draft_approval(msg, context))
    payload = messages.send.call_args.kwargs["body"]
    messages.send.assert_called_once()
    mime = message_from_bytes(base64.urlsafe_b64decode(payload["raw"]))
    assert payload["threadId"] == "thread1"
    assert mime["To"] == "reply@example.com" and mime["In-Reply-To"] == "<id@example.com>"
    assert mime.get_payload(decode=True).decode() == args["body"]


def test_cancelled_or_stale_guest_invites_never_run(monkeypatch):
    monkeypatch.setattr(main, "ALLOWED_USER_IDS", {7})
    create = AsyncMock(return_value="created")
    monkeypatch.setattr(tools, "create_event", create)
    msg, context = update(), SimpleNamespace(chat_data={})
    args = {"title": "Meeting", "start": "2026-09-01T14:00", "attendees": ["guest@example.com"]}
    async def present(channel, preview, payload):
        return await main._show_draft(msg, channel, preview, payload)

    assert "approval" in asyncio.run(agent._execute_tool("create_event", args, 7))
    asyncio.run(agent._execute_tool("create_event", args, 7, present))
    from modules import _outgoing
    msg.callback_query.data = msg.message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
    unauthorized = update(user=8, chat=7)
    unauthorized.callback_query.data = msg.callback_query.data
    asyncio.run(main.handle_draft_approval(unauthorized, context))
    assert _outgoing.get_outgoing_context(7)["status"] == "pending"
    with tools.get_db() as conn:
        conn.execute("UPDATE outgoing_drafts SET expires_at=0")
    asyncio.run(main.handle_draft_approval(msg, context))
    assert _outgoing.get_outgoing_context(7)["status"] == "expired"
    asyncio.run(agent._execute_tool("create_event", args, 7, present))
    msg.callback_query.data = msg.message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard[0][1].callback_data
    asyncio.run(main.handle_draft_approval(msg, context))
    assert _outgoing.get_outgoing_context(7)["status"] == "cancelled"
    create.assert_not_awaited()
