"""Select the model for each workload. Global local mode keeps model calls on Ollama."""
import asyncio
import json
import logging
import os
import time
from contextvars import ContextVar
from pathlib import Path

import anthropic
import httpx

logger = logging.getLogger(__name__)
# Concurrent research and chat calls can use different backends.
tool_caller = ContextVar("tool_caller", default="main")
BACKEND = os.getenv("LLM_BACKEND", "anthropic").strip().lower()
if BACKEND not in {"anthropic", "local"}:
    raise ValueError("Set LLM_BACKEND to anthropic or local")
LOCAL = BACKEND == "local"
LOCAL_MODEL = os.getenv("LOCAL_MODEL", "qwen3:8b").strip()
LOCAL_BASE_URL = os.getenv("LOCAL_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
LOCAL_VISION = os.getenv("LOCAL_VISION", "false").lower() in {"true", "1"}
LOCAL_THINKING = os.getenv("LOCAL_THINKING", "off").lower()
if LOCAL_THINKING not in {"off", "low", "medium", "high"}:
    raise ValueError("Set LOCAL_THINKING to off, low, medium or high")
CLOUD_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
CONTEXT_MARKER = "\n\n## Current context\n"
HEALTH_FILE = Path(__file__).with_name(".mygg-inference.json")


def uses_local(caller="main"):
    selection = os.getenv(f"LLM_BACKEND_{caller.upper()}", BACKEND).lower()
    if selection not in {"anthropic", "local"}:
        raise ValueError(f"Check LLM_BACKEND_{caller.upper()} in .env.")
    return LOCAL or selection == "local"


def model_for(caller):
    return LOCAL_MODEL if uses_local(caller) else CLOUD_MODEL


MODEL = model_for("main")
if not LOCAL_MODEL or LOCAL_MODEL.lower().endswith(("-cloud", ":cloud")):
    raise ValueError("LOCAL_MODEL must name an installed local Ollama model")
_local = anthropic.AsyncAnthropic(
    base_url=LOCAL_BASE_URL, api_key="ollama", auth_token="ollama",
    http_client=httpx.AsyncClient(trust_env=False), timeout=300.0, max_retries=0,
)
_cloud = None if LOCAL else anthropic.AsyncAnthropic()
client = _local if uses_local() else _cloud
_local_slot = asyncio.Semaphore(1)


def record_inference(timed_out):
    try:
        if not timed_out:
            HEALTH_FILE.unlink(missing_ok=True)
            return
        now = time.time()
        count = 1
        if HEALTH_FILE.exists():
            try:
                prior = json.loads(HEALTH_FILE.read_text())
                if prior["base_url"] == LOCAL_BASE_URL and 0 <= now - prior["failed_at"] <= 900:
                    count += int(prior["consecutive_timeouts"])
            except (KeyError, ValueError, TypeError):
                pass
        tmp = HEALTH_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(base_url=LOCAL_BASE_URL, failed_at=now, consecutive_timeouts=count)))
        tmp.replace(HEALTH_FILE)
    except OSError:
        logger.warning("Could not update the local inference status.")


def text(response) -> str:
    return "\n".join(b.text for b in (response.content or []) if b.type == "text").strip()


async def create(*, messages, max_tokens, model=None, system=None, tools=None, container=None, caller="main"):
    local = uses_local(caller)
    selected = LOCAL_MODEL if local else (model or CLOUD_MODEL)
    params = {"model": selected, "messages": messages, "max_tokens": max_tokens}
    if system is not None:
        params["system"] = system
    if tools is not None:
        params["tools"] = tools
    if local:
        if tools is not None:
            from modules._web import localize_tools
            params["tools"] = localize_tools(tools)
        if LOCAL_THINKING == "off" or caller in {"meetings", "whatsapp"}:
            params["thinking"] = {"type": "disabled"}
        else:
            params["output_config"] = {"effort": LOCAL_THINKING}
        params["max_tokens"] = min(max_tokens, 6000)
        try:
            await asyncio.wait_for(_local_slot.acquire(), timeout=60)
        except TimeoutError as exc:
            raise TimeoutError("The local model is busy. Try again in a moment.") from exc
        try:
            result = await _local.messages.create(**params)
            record_inference(False)
            return result
        except anthropic.APITimeoutError:
            record_inference(True)
            raise
        finally:
            _local_slot.release()
    if tools is not None:
        prefix, marker, current = system.partition(CONTEXT_MARKER)
        params["system"] = [{"type": "text", "text": prefix, "cache_control": {"type": "ephemeral"}}]
        if marker:
            params["system"].append({"type": "text", "text": marker.strip() + "\n" + current})
        last = messages[-1]
        content = last["content"]
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
        if blocks and blocks[-1].get("type") in {"text", "tool_result", "image", "document"}:
            blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
            params["messages"] = [*messages[:-1], {**last, "content": blocks}]
        if container:
            params["container"] = container
        return await _cloud.beta.messages.create(
            **params, betas=["compact-2026-01-12"], thinking={"type": "adaptive"},
            context_management={"edits": [{"type": "compact_20260112"}]},
        )
    return await _cloud.messages.create(**params)
