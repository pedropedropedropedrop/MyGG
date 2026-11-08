"""HTTP server that receives WhatsApp messages from the Node.js bridge."""

import hmac
import os
import logging
import json
import asyncio
from aiohttp import web

import tools

logger = logging.getLogger(__name__)

BRIDGE_SECRET = os.getenv("WHATSAPP_BRIDGE_SECRET", "").strip()
BRIDGE_PORT = int(os.getenv("WHATSAPP_BRIDGE_PORT", "8787"))

def _check_auth(request: web.Request) -> bool:
    """Validate the bridge's auth token (timing-safe)."""
    if not BRIDGE_SECRET:
        return False
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {BRIDGE_SECRET}"
    return hmac.compare_digest(auth.encode(), expected.encode())


async def handle_incoming(request: web.Request) -> web.Response:
    """Receive a WhatsApp message from the bridge and store it."""
    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")

    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.Response(status=400, text="Invalid JSON")

    if not isinstance(data, dict):
        return web.Response(status=400, text="Expected a JSON object")

    # Validate required fields
    required = ["chat_jid", "sender_jid", "timestamp", "whatsapp_message_id"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return web.Response(status=400, text=f"Missing fields: {', '.join(missing)}")

    # Store in SQLite (INSERT OR IGNORE for dedup on whatsapp_message_id)
    try:
        def _store():
            conn = tools.get_db()
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO whatsapp_messages
                       (whatsapp_message_id, chat_jid, chat_name, sender_jid, sender_name,
                        message_text, message_type, is_from_me, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        data["whatsapp_message_id"],
                        data["chat_jid"],
                        data.get("chat_name"),
                        data["sender_jid"],
                        data.get("sender_name"),
                        data.get("message_text"),
                        data.get("message_type", "text"),
                        1 if data.get("is_from_me") else 0,
                        data["timestamp"],
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_store)
    except Exception:
        logger.exception("Failed to store WhatsApp message")
        return web.Response(status=500, text="Storage error")

    return web.Response(status=200, text="OK")


async def handle_health(request: web.Request) -> web.Response:
    """Health check endpoint."""
    return web.Response(status=200, text="OK")


async def start_server():
    """Start the aiohttp server as a background task."""
    if not BRIDGE_SECRET:
        logger.info("WhatsApp is disabled until WHATSAPP_BRIDGE_SECRET is set")
        return
    app = web.Application()
    app.router.add_post("/whatsapp/incoming", handle_incoming)
    app.router.add_get("/whatsapp/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", BRIDGE_PORT)
    await site.start()
    logger.info(f"WhatsApp HTTP server listening on 127.0.0.1:{BRIDGE_PORT}")
