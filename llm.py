"""Route all text generation to the backend chosen by the owner."""

import asyncio
import os

import anthropic
import httpx

BACKEND = os.getenv("LLM_BACKEND", "anthropic").strip().lower()
if BACKEND not in {"anthropic", "local"}:
    raise ValueError("Set LLM_BACKEND to anthropic or local")

LOCAL = BACKEND == "local"
MODEL = (os.getenv("LOCAL_MODEL", "qwen3:8b") if LOCAL
         else os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")).strip()
if LOCAL and (not MODEL.strip() or MODEL.lower().endswith(("-cloud", ":cloud"))):
    raise ValueError("LOCAL_MODEL must name an installed local Ollama model")

if LOCAL:
    client = anthropic.AsyncAnthropic(
        base_url="http://127.0.0.1:11434", api_key="ollama", auth_token="ollama",
        http_client=httpx.AsyncClient(trust_env=False),
        timeout=300.0, max_retries=0,
    )
else:
    client = anthropic.AsyncAnthropic()

_local_slot = asyncio.Semaphore(1)


def text(response) -> str:
    """Thinking models may return a reasoning block before their answer."""
    return "\n".join(b.text for b in (response.content or []) if b.type == "text").strip()


async def create(*, messages, max_tokens, model=None, system=None, tools=None, container=None):
    params = {"model": MODEL if LOCAL else (model or MODEL),
              "messages": messages, "max_tokens": max_tokens}
    if system is not None:
        params["system"] = system
    if tools is not None:
        params["tools"] = tools
    if LOCAL:
        # Ollama handles ordinary function tools. Typed tools invoke hosted services.
        if tools is not None:
            params["tools"] = [tool for tool in tools if "type" not in tool]
        params["thinking"] = {"type": "disabled"}
        params["max_tokens"] = min(max_tokens, 4096)
        try:
            await asyncio.wait_for(_local_slot.acquire(), timeout=60)
        except TimeoutError as exc:
            raise TimeoutError("The local model is busy. Try again in a moment.") from exc
        try:
            return await client.messages.create(**params)
        finally:
            _local_slot.release()
    if tools is not None:
        params["system"] = [{"type": "text", "text": system,
                             "cache_control": {"type": "ephemeral"}}]
        if container:
            params["container"] = container
        return await client.beta.messages.create(
            **params, betas=["compact-2026-01-12"], thinking={"type": "adaptive"},
            context_management={"edits": [{"type": "compact_20260112"}]},
        )
    return await client.messages.create(**params)
