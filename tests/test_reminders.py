import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import main
import tools


def test_reminders_retry_failed_deliveries_and_drop_revoked_recipients(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "DB_PATH", tmp_path / "pa.db")
    monkeypatch.setattr(main, "ALLOWED_USER_IDS", {1, 2})
    tools.init_db()

    async def run():
        for user_id in (1, 2, 3):
            await tools.set_reminder("check", "2000-01-01T18:00:00", user_id)
        await tools.set_reminder("later", "2099-01-01T10:00:00+02:00", 2)
        send = AsyncMock(side_effect=[OSError("offline"), None, None])
        context = SimpleNamespace(bot=SimpleNamespace(send_message=send))
        await main.check_reminders(context)
        assert [row[1] for row in await tools.check_due_reminders()] == [1]
        await main.check_reminders(context)
        assert await tools.check_due_reminders() == []
        assert [c.kwargs["chat_id"] for c in send.await_args_list] == [1, 2, 1]

    asyncio.run(run())
