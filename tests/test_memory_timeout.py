"""Memory's native HTTP calls must stop even after an outer coroutine times out."""
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

import tools


@pytest.fixture
def memory_clients(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={})

    with httpx.Client(timeout=None, transport=httpx.MockTransport(respond)) as embed_http, \
            httpx.Client(timeout=None, transport=httpx.MockTransport(respond)) as llm_http:
        chat = Mock(return_value="synthetic response")
        memory = SimpleNamespace(
            embedding_model=SimpleNamespace(client=SimpleNamespace(_client=embed_http)),
            llm=SimpleNamespace(client=SimpleNamespace(_client=llm_http, chat=chat)),
        )
        module = ModuleType("mem0")
        factory = Mock(return_value=memory)
        module.Memory = SimpleNamespace(from_config=factory)
        monkeypatch.setitem(sys.modules, "mem0", module)
        monkeypatch.setattr(tools, "_memory_client", None)

        yield memory, factory, chat, requests


def test_memory_native_timeouts_and_thinking_wrapper(memory_clients):
    memory, factory, chat, requests = memory_clients
    assert tools._get_memory() is memory
    memory.embedding_model.client._client.post("http://local.invalid/api/embeddings")
    memory.llm.client._client.post("http://local.invalid/api/chat")
    assert requests[0].extensions["timeout"] == {"connect": 5, "read": 10, "write": 10, "pool": 5}
    assert requests[1].extensions["timeout"] == {"connect": 5, "read": 300, "write": 300, "pool": 5}
    memory.llm.client.chat(model="synthetic", messages=[])
    chat.assert_called_once_with(think=False, model="synthetic", messages=[])
    memory.llm.client.chat(model="synthetic", think=True)
    assert chat.call_args.kwargs["think"] is True
    assert tools._get_memory() is memory
    factory.assert_called_once()


@pytest.mark.parametrize("provider", ["embedding_model", "llm"])
def test_unknown_mem0_client_layout_fails_instead_of_caching_infinite_timeout(memory_clients, provider):
    memory, _, _, _ = memory_clients
    getattr(memory, provider).client._client = SimpleNamespace(timeout=None)
    with pytest.raises(RuntimeError, match="timeout.*mem0"):
        tools._get_memory()
    assert tools._memory_client is None
