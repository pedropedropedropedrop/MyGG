"""Tests for module auto-discovery system."""

import asyncio
import shutil
from pathlib import Path

import pytest

MODULES_DIR = Path(__file__).parent.parent / "modules"
TMP_DIR = Path(__file__).parent / "tmp_modules"


@pytest.fixture(autouse=True)
def clean_registry():
    """Reset module registry before each test."""
    import modules
    modules._modules.clear()
    modules._tool_definitions.clear()
    modules._tool_dispatch.clear()
    yield
    modules._modules.clear()
    modules._tool_definitions.clear()
    modules._tool_dispatch.clear()


@pytest.fixture
def fake_module(tmp_path):
    """Write a valid module file into modules/ and clean up after."""
    mod_file = MODULES_DIR / "test_fake.py"
    mod_file.write_text('''
TOOL_DEFINITIONS = [
    {
        "name": "fake_tool",
        "description": "A fake tool for testing.",
        "input_schema": {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
    },
]

async def execute_tool(name, input_data):
    if name == "fake_tool":
        return f"fake: {input_data['msg']}"
    return f"unknown: {name}"

async def heartbeat_contributions():
    return "Fake heartbeat data"
''')
    yield mod_file
    mod_file.unlink(missing_ok=True)
    # Clean up __pycache__ for the fake module
    cache = MODULES_DIR / "__pycache__"
    for f in cache.glob("test_fake*"):
        f.unlink(missing_ok=True)


def test_discover_empty():
    """Discovery with no module files returns empty."""
    import modules
    modules.discover()
    assert modules.get_module_names() == []
    assert modules.get_tool_definitions() == []


def test_discover_finds_module(fake_module):
    """Discovery picks up a valid module and registers its tools."""
    import modules
    modules.discover()
    assert "test_fake" in modules.get_module_names()
    tool_names = [t["name"] for t in modules.get_tool_definitions()]
    assert "fake_tool" in tool_names


def test_discover_skips_underscore():
    """Files starting with _ are skipped (e.g. _template.py)."""
    import modules
    modules.discover()
    assert "_template" not in modules.get_module_names()


def test_execute_module_tool(fake_module):
    """Module tool dispatch works."""
    import modules
    modules.discover()
    result = asyncio.run(
        modules.execute_tool("fake_tool", {"msg": "hello"})
    )
    assert result == "fake: hello"


def test_execute_unknown_returns_none(fake_module):
    """Unknown tool name returns None (not a module tool)."""
    import modules
    modules.discover()
    result = asyncio.run(
        modules.execute_tool("nonexistent_tool", {})
    )
    assert result is None


def test_heartbeat_contributions(fake_module):
    """Modules with heartbeat_contributions() are collected."""
    import modules
    modules.discover()
    contribs = asyncio.run(
        modules.get_heartbeat_contributions()
    )
    assert "test_fake" in contribs
    assert contribs["test_fake"] == "Fake heartbeat data"


def test_discover_skips_invalid_module():
    """Module missing required exports is skipped."""
    bad_file = MODULES_DIR / "test_bad.py"
    bad_file.write_text("X = 42\n")
    try:
        import modules
        modules.discover()
        assert "test_bad" not in modules.get_module_names()
    finally:
        bad_file.unlink(missing_ok=True)
        cache = MODULES_DIR / "__pycache__"
        for f in cache.glob("test_bad*"):
            f.unlink(missing_ok=True)
