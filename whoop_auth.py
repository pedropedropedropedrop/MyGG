"""One-time OAuth consent helper for Whoop integration.

Run this once after setting WHOOP_CLIENT_ID / WHOOP_CLIENT_SECRET / WHOOP_REDIRECT_URI
in .env. It opens your browser, waits for consent, then writes
.whoop_token.json next to this script. modules/whoop.py reads and refreshes that
file on every API call.

Usage:
    python whoop_auth.py
"""

import asyncio
import json
import logging
import os
import secrets
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

import aiohttp
from aiohttp import web
from dotenv import load_dotenv

from utils import write_secret_file
from constants import (
    WHOOP_API_TIMEOUT_SECONDS,
    WHOOP_OAUTH_AUTHZ_URL,
    WHOOP_OAUTH_CALLBACK_PORT,
    WHOOP_OAUTH_CALLBACK_TIMEOUT_SECONDS,
    WHOOP_OAUTH_SCOPES,
    WHOOP_OAUTH_TOKEN_URL,
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("whoop_auth")

TOKEN_FILE = Path(__file__).parent / ".whoop_token.json"


def _read_env() -> dict:
    """Pull and validate the three Whoop OAuth env vars."""
    cfg = {
        "client_id": os.getenv("WHOOP_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("WHOOP_CLIENT_SECRET", "").strip(),
        "redirect_uri": os.getenv("WHOOP_REDIRECT_URI", "").strip(),
    }
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        raise ValueError(f"Set the missing WHOOP values in .env: {', '.join(missing)}")
    return cfg


def _build_authz_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Build the Whoop authorization URL the user must open in a browser."""
    assert client_id and redirect_uri and state, "authz url requires non-empty inputs"
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": WHOOP_OAUTH_SCOPES,
        "state": state,
    }
    return f"{WHOOP_OAUTH_AUTHZ_URL}?{urllib.parse.urlencode(params)}"


async def _exchange_code_for_token(cfg: dict, code: str) -> dict:
    """POST the auth code to Whoop and receive access + refresh tokens."""
    assert code, "auth code is required"
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg["redirect_uri"],
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
    }
    timeout = aiohttp.ClientTimeout(total=WHOOP_API_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(WHOOP_OAUTH_TOKEN_URL, data=payload) as resp:
            if resp.status != 200:
                raise RuntimeError(f"WHOOP token exchange failed (HTTP {resp.status}). Check the app credentials and reconnect.")
            return await resp.json()


def _persist_token(token_response: dict) -> None:
    """Save access_token, refresh_token, and absolute expires_at to disk."""
    if not token_response.get("access_token") or not token_response.get("refresh_token"):
        raise ValueError("WHOOP did not return an offline grant. Run python whoop_auth.py to reconnect.")
    expires_in = int(token_response.get("expires_in", 0))
    record = {
        "access_token": token_response["access_token"],
        "refresh_token": token_response.get("refresh_token", ""),
        "expires_at": time.time() + expires_in,
        "scope": token_response.get("scope", WHOOP_OAUTH_SCOPES),
        "token_type": token_response.get("token_type", "bearer"),
    }
    write_secret_file(TOKEN_FILE, json.dumps(record, indent=2))
    logger.info(f"Saved Whoop token to {TOKEN_FILE}")


async def _run_callback_server(state: str, auth_url: str) -> str:
    """Run a single-shot aiohttp server, return the auth code once received."""
    assert state, "state token required"
    code_future: asyncio.Future = asyncio.get_running_loop().create_future()

    async def handler(request: web.Request) -> web.Response:
        if code_future.done():
            return web.Response(text="Already received a code; you may close this tab.")
        got_state = request.query.get("state", "")
        got_code = request.query.get("code", "")
        err = request.query.get("error", "")
        if not secrets.compare_digest(got_state.encode(), state.encode()):
            return web.Response(text="State mismatch", status=400)
        if err:
            code_future.set_exception(RuntimeError("WHOOP authorization was declined."))
            return web.Response(text="WHOOP authorization was declined.", status=400)
        if not got_code:
            code_future.set_exception(RuntimeError("No code in callback"))
            return web.Response(text="Missing code", status=400)
        code_future.set_result(got_code)
        return web.Response(
            text="Authorization received. You can close this tab and return to the terminal.",
            content_type="text/html",
        )

    app = web.Application()
    app.router.add_get("/callback", handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", WHOOP_OAUTH_CALLBACK_PORT)
    await site.start()
    try:
        if not webbrowser.open(auth_url):
            print(f"Open the authorization URL in a browser on this computer:\n{auth_url}")
        return await asyncio.wait_for(code_future, timeout=WHOOP_OAUTH_CALLBACK_TIMEOUT_SECONDS)
    finally:
        await runner.cleanup()


async def _main() -> int:
    cfg = _read_env()
    state = secrets.token_urlsafe(24)
    url = _build_authz_url(cfg["client_id"], cfg["redirect_uri"], state)

    logger.info("Opening browser for Whoop consent…")

    try:
        code = await _run_callback_server(state, url)
    except asyncio.TimeoutError:
        logger.error("Timed out waiting for Whoop consent.")
        return 1
    except Exception:
        logger.exception("OAuth callback failed")
        return 1

    try:
        token_response = await _exchange_code_for_token(cfg, code)
        _persist_token(token_response)
    except Exception:
        logger.exception("Token exchange failed")
        return 1

    logger.info("All set — GG can now read your Whoop data.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
