"""WHOOP rotation and consent tests use synthetic tokens and no external accounts."""

import asyncio
import time

import httpx
import pytest
from modules import whoop


@pytest.mark.parametrize("initially_expired", [True, False])
def test_concurrent_api_requests_rotate_the_token_once(monkeypatch, tmp_path, initially_expired):
    monkeypatch.setattr(whoop, "TOKEN_FILE", tmp_path / "tokens.json")
    monkeypatch.setattr(whoop, "_refresh_lock", asyncio.Lock())
    monkeypatch.setenv("WHOOP_CLIENT_ID", "fixture")
    monkeypatch.setenv("WHOOP_CLIENT_SECRET", "fixture")
    whoop._save_token({"access_token": "old", "refresh_token": "once",
                       "expires_at": 0 if initially_expired else time.time() + 3600})
    refresh_count = 0

    async def serve(req):
        nonlocal refresh_count
        if req.method == "POST":
            refresh_count += 1
            assert b"refresh_token=once" in req.content and b"scope=offline" in req.content
            await asyncio.sleep(0.01)
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "rotated", "expires_in": 3600})
        await asyncio.sleep(0)
        return httpx.Response(200, json={"data": 1}) if req.headers["Authorization"] == "Bearer new" else httpx.Response(401)

    client = httpx.AsyncClient
    monkeypatch.setattr(whoop.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(serve), **kw))

    async def fetch():
        return await asyncio.gather(*(whoop._authed_request("GET", "/test") for _ in range(4)))
    assert asyncio.run(fetch()) == [{"data": 1}] * 4
    assert refresh_count == 1
    assert whoop._load_token()["refresh_token"] == "rotated"


def test_rejected_refresh_prompts_reauthorization_without_error_body(monkeypatch, tmp_path):
    monkeypatch.setenv("WHOOP_CLIENT_ID", "fixture")
    monkeypatch.setenv("WHOOP_CLIENT_SECRET", "fixture")
    monkeypatch.setattr(whoop, "TOKEN_FILE", tmp_path / "tokens.json")
    def reject(req):
        return httpx.Response(400, text="fixture-secret-in-provider-response")
    client = httpx.AsyncClient
    monkeypatch.setattr(whoop.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(reject), **kw))
    with pytest.raises(whoop.WhoopNotConnectedError, match="whoop_auth.py") as err:
        asyncio.run(whoop._refresh_token({"refresh_token": "expired"}))
    assert "fixture-secret" not in str(err)
    assert not whoop.TOKEN_FILE.exists()

def test_callback_ignores_wrong_state_and_keeps_authorization_code_out_of_logs(monkeypatch, caplog):
    import socket
    import aiohttp
    import whoop_auth

    async def scenario():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            sock.listen()
            monkeypatch.setattr(whoop_auth.web, "TCPSite", lambda runner, *_: whoop_auth.web.SockSite(runner, sock))

            async def simulate_browser():
                async with aiohttp.ClientSession() as client:
                    url = f"http://127.0.0.1:{port}/callback"
                    async with client.get(url, params={"state": "wrong", "error": "denied"}) as resp:
                        assert resp.status == 400
                    async with client.get(url, params={"state": "matching", "code": "fixture-oauth-code"}) as resp:
                        assert resp.status == 200

            task = None
            def launch(_):
                nonlocal task
                task = asyncio.create_task(simulate_browser())
                return True
            monkeypatch.setattr(whoop_auth.webbrowser, "open", launch)
            code = await whoop_auth._run_callback_server("matching", "https://provider.invalid/")
            await task
            return code

    caplog.set_level("INFO")
    assert asyncio.run(scenario()) == "fixture-oauth-code"
    assert "fixture-oauth-code" not in caplog.text
