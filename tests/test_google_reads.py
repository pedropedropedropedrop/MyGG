"""Google parity tests with synthetic accounts, paged responses and nested email."""

import asyncio
import base64
from unittest.mock import Mock, MagicMock

import pytest

import setup_calendar
import tools


def test_paged_calendar_sources_sorted_in_owner_timezone(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "DB_PATH", tmp_path / "local.db")
    tools.init_db()
    service = Mock()
    def fetch(**params):
        events = ([{"id": "a", "summary": "Later", "start": {"dateTime": "2026-09-01T08:30:00-07:00"}}]
                  if "pageToken" not in params else
                  [{"id": "b", "summary": "Earlier", "start": {"dateTime": "2026-09-01T10:00:00-04:00"}}])
        response = {"items": events}
        if "pageToken" not in params:
            response["nextPageToken"] = "next"
        return Mock(execute=lambda: response)
    service.events().list.side_effect = fetch
    monkeypatch.setattr(tools, "_get_calendar_service", lambda account: service)
    text = asyncio.run(tools.get_calendar("2026-09-01", account="personal"))
    assert text.index("Earlier") < text.index("Later")
    assert "calendar:personal:b" in text and "calendar:personal:a" in text
    assert service.events().list.call_count == 2


def test_thread_latest_delivered_state_ignores_newer_draft_and_reads_nested_html(monkeypatch):
    service = Mock()
    service.users().messages().get().execute.return_value = {"threadId": "thread"}
    body = {"mimeType": "text/html", "body": {"data": base64.urlsafe_b64encode(b"<p>Nested reply</p>").decode().rstrip("=")}}
    for _ in range(3):
        body = {"mimeType": "multipart/mixed", "parts": [body]}
    service.users().threads().get().execute.return_value = {"messages": [
        {"id": "draft", "internalDate": "3", "labelIds": ["DRAFT"]},
        {"id": "reply", "internalDate": "2", "labelIds": ["SENT"], "payload": body},
        {"id": "first", "internalDate": "1", "labelIds": ["INBOX"]}]}
    monkeypatch.setattr(tools, "_get_gmail_service", lambda account: service)
    result = asyncio.run(tools.read_email("personal:first"))
    assert "gmail:personal:thread" in result and "latest message is from the user" in result
    assert "Unsent draft" in result and "Nested reply" in result
    assert result.index("personal:reply") < result.index("personal:first")
    with pytest.raises(ValueError, match="Unknown Google account"):
        asyncio.run(tools.read_email("missing:message"))


def test_configured_address_rejects_wrong_account_before_token_write(monkeypatch):
    from google.auth.transport import requests
    session = MagicMock()
    session.__enter__.return_value.get.return_value.json.return_value = {"emailAddress": "owner@example.org"}
    monkeypatch.setattr(requests, "AuthorizedSession", lambda creds: session)
    assert setup_calendar._verify_google_identity(object(), "Owner@Example.org") == "owner@example.org"
    with pytest.raises(ValueError, match="different account"):
        setup_calendar._verify_google_identity(object(), "other@example.org")
