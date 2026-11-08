"""Synthetic private-account parity checks."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import agent
import main
import tools
from modules import _outgoing, feedback


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "DB_PATH", tmp_path / "test.db")
    tools.init_db()
    feedback.init_feedback()
    conn = tools.get_db()
    try:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for jid, label in [("101@lid", "Design team"), ("202-303@g.us", "Design project")]:
            conn.execute("""INSERT INTO whatsapp_messages
                (whatsapp_message_id, chat_jid, chat_name, sender_jid, message_text, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)""", ("fixture-" + jid, jid, label, jid, "draft deadline", now))
        conn.commit()
    finally:
        conn.close()


def test_named_whatsapp_recipients_are_resolved_before_approval(monkeypatch, db):
    monkeypatch.setenv("WHATSAPP_BRIDGE_SECRET", "example")
    assert tools._resolve_wa_jid("Design")[0] is None
    assert tools._resolve_wa_jid("Design project") == ("202-303@g.us", "Design project")
    assert tools._resolve_wa_jid("unknown")[0] is None
    assert tools._resolve_wa_jid("status@broadcast")[0] is None
    delivered = AsyncMock(return_value="Sent to Design project on WhatsApp (receipt synthetic).")
    monkeypatch.setattr(tools, "_deliver_whatsapp", delivered)
    async def show(channel, preview, payload):
        token = _outgoing.prepare(7, channel, preview, payload)
        _outgoing.presented(7, token)
        return token
    args = {"chat": "Design project", "text": "Meet at 3?"}
    assert "approval" in asyncio.run(agent._execute_tool("send_whatsapp", args, 7))
    token = asyncio.run(agent._execute_tool("send_whatsapp", args, 7, show))
    assert "202-303@g.us" in _outgoing.get_outgoing_context(7)["preview"]
    assert "Meet at 3?" in _outgoing.get_outgoing_context(7)["preview"]
    delivered.assert_not_awaited()
    assert "no longer pending" in asyncio.run(_outgoing.resolve(8, token, "send"))
    assert "receipt" in asyncio.run(_outgoing.resolve(7, token, "send"))
    assert "no longer pending" in asyncio.run(_outgoing.resolve(7, token, "send"))
    delivered.assert_awaited_once_with({"recipient": "202-303@g.us", "label": "Design project", "text": "Meet at 3?"})
    assert _outgoing.get_outgoing_context(7)["status"] == "sent"


def test_whatsapp_search_includes_date_scope_chat_and_stable_refs(db):
    rows = asyncio.run(tools.search_whatsapp("deadline", "101@lid"))
    assert "fixture-101" in rows and "202-303" not in rows
    summary = asyncio.run(tools.get_whatsapp_summary("202-303@g.us"))
    assert "fixture-202-303" in summary and "101@lid" not in summary


def test_local_pdf_upload_passes_text_and_page_to_agent(monkeypatch, tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(200, 100)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 15 30 Td (Fixture document) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    path = tmp_path / "sample.pdf"
    writer.write(path)
    monkeypatch.setattr(main.llm, "LOCAL", True)
    handler = AsyncMock()
    monkeypatch.setattr(main, "_process_and_respond", handler)
    msg = SimpleNamespace()
    asyncio.run(main._handle_pdf_document(path, "sample.pdf", "Summarize this", msg, None))
    text = handler.await_args.args[2]
    assert "Page 1" in text and "Fixture document" in text and "Summarize this" in text
