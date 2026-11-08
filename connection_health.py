"""Health checks and optional warm-up jobs for the user's configured services."""

import asyncio
from datetime import datetime, time
import json
import logging
import os
from pathlib import Path

import httpx

import llm
import tools
from utils import write_secret_file

LOG = logging.getLogger(__name__)
STATE = Path(__file__).with_name(".mygg-health.json")


async def _http(url):
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get(url)
        response.raise_for_status()


def _google(account):
    service = tools._get_calendar_service(account)
    if not service:
        raise RuntimeError("account unavailable")
    service.calendarList().list(maxResults=1).execute()


async def _whatsapp():
    status = await tools.whatsapp_status()
    if status.get("connection") != "open":
        raise RuntimeError("device disconnected")


async def probe():
    from modules import whoop

    checks = []
    if tools.memory_enabled():
        checks.append(("Qdrant memory", _http("http://{}:{}/healthz".format(
            os.getenv("QDRANT_HOST", "localhost"), os.getenv("QDRANT_PORT", "6333")))))
        memory_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        checks.append(("Ollama memory", _http(memory_url + "/api/tags")))
    if any(llm.uses_local(role) for role in ("main", "subagent", "meetings", "whatsapp")):
        checks.append(("Ollama chat", _http(llm.LOCAL_BASE_URL.rstrip("/") + "/api/tags")))
    for account in tools._available_accounts():
        checks.append((f"Google ({account})", asyncio.to_thread(_google, account)))
    if whoop.TOKEN_FILE.exists():
        checks.append(("WHOOP", whoop._fetch_profile()))
    if os.getenv("WHATSAPP_BRIDGE_SECRET", "").strip():
        checks.append(("WhatsApp", _whatsapp()))
    results = await asyncio.gather(*(asyncio.wait_for(job, 12) for _, job in checks),
                                   return_exceptions=True)
    return {name: not isinstance(result, BaseException)
            for (name, _), result in zip(checks, results)}


def report(current, previous, today):
    old = previous.get("services", {})
    lines = []
    for name, ok in current.items():
        if (name in old and old[name] != ok) or (not ok and name not in old):
            lines.append(f"{name} has recovered." if ok else f"{name} needs attention.")
    digest = previous.get("digest")
    if today.weekday() == 0 and digest != today.isoformat() and current:
        digest = today.isoformat()
        lines.append("Weekly connection check\n" + "\n".join(
            f"{name}: {'connected' if ok else 'unavailable'}" for name, ok in current.items()))
    return lines, {"services": current, "digest": digest}


async def check_connections(context):
    try:
        previous = json.loads(STATE.read_text(encoding="utf-8"))
        if not isinstance(previous, dict) or not isinstance(previous.get("services", {}), dict):
            previous = {}
    except (OSError, ValueError):
        previous = {}
    lines, state = report(await probe(), previous, datetime.now(tools.TZ).date())
    if lines:
        # Keep the previous state if delivery fails. The next probe retries the notice.
        await context.bot.send_message(chat_id=context.job.data, text="\n".join(lines))
    write_secret_file(STATE, json.dumps(state))


async def pin_model(context):
    try:
        async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
            response = await client.post(llm.LOCAL_BASE_URL.rstrip("/") + "/api/generate",
                                         json={"model": llm.LOCAL_MODEL, "keep_alive": -1, "stream": False})
            response.raise_for_status()
    except httpx.HTTPError as exc:
        LOG.warning("Could not keep local model loaded (%s)", type(exc).__name__)


async def warm_memory(context):
    try:
        await asyncio.wait_for(asyncio.to_thread(tools._get_memory), timeout=90)
    except Exception as exc:
        LOG.warning("Memory warm-up unavailable (%s)", type(exc).__name__)


def schedule(app, tz, owner):
    if owner:
        app.job_queue.run_once(check_connections, when=120, data=owner)
        app.job_queue.run_daily(check_connections, time=time(8, 0, tzinfo=tz), data=owner)
    if os.getenv("MYGG_PIN_MODEL", "").strip().lower() == "true":
        app.job_queue.run_repeating(pin_model, interval=1800, first=1)
    if tools.memory_enabled() and os.getenv("MYGG_WARM_MEMORY", "").strip().lower() == "true":
        app.job_queue.run_once(warm_memory, when=3)
