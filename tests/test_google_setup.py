import os
import asyncio
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

from utils import write_secret_file
import tools


def test_calendar_invite_requests_guest_notification(monkeypatch):
    service = Mock()
    monkeypatch.setattr(tools, "_get_calendar_service", lambda account: service)
    asyncio.run(tools.create_event("Review", "2026-11-10T12:00", attendees=["friend@example.org"]))
    params = service.events.return_value.insert.call_args.kwargs
    assert params["sendUpdates"] == "all"
    assert params["body"]["attendees"] == [{"email": "friend@example.org"}]


def test_setup_and_bot_use_the_same_named_accounts():
    env = dict(os.environ, GOOGLE_EXTRA_ACCOUNTS="work, club", GOOGLE_ACCOUNT_WORK_LABEL="Office")
    code = """
import setup_calendar
from tools import GOOGLE_ACCOUNTS
assert setup_calendar.GOOGLE_ACCOUNTS is GOOGLE_ACCOUNTS
assert GOOGLE_ACCOUNTS["work"]["label"] == "Office"
assert GOOGLE_ACCOUNTS["club"]["token_path"].name == "token_club.json"
assert GOOGLE_ACCOUNTS["personal"]["token_path"].name == "token.json"
"""
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-c", code], cwd=root, env=env, check=True)
    env["GOOGLE_EXTRA_ACCOUNTS"] = "../bad"
    invalid = subprocess.run([sys.executable, "-c", "import tools"], cwd=root, env=env,
                             capture_output=True, text=True)
    assert invalid.returncode and "GOOGLE_EXTRA_ACCOUNTS" in invalid.stderr


def test_private_token_replacement(tmp_path):
    path = tmp_path / "token.json"
    write_secret_file(path, "first")
    write_secret_file(path, "second")
    assert path.read_text() == "second"
    assert list(tmp_path.iterdir()) == [path]
    if os.name == "posix":
        assert path.stat().st_mode & 0o077 == 0
