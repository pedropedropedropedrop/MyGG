"""Module auto-discovery and registry for GG domain modules."""

import importlib
import logging
import pkgutil
from pathlib import Path
from types import ModuleType

logger = logging.getLogger(__name__)

_modules: dict[str, ModuleType] = {}
_tool_definitions: list[dict] = []
_tool_dispatch: dict[str, ModuleType] = {}


def discover():
    """Scan modules/ for .py files, import each, register their tools."""
    _modules.clear()
    _tool_definitions.clear()
    _tool_dispatch.clear()

    package_dir = Path(__file__).parent

    for _, name, _ in pkgutil.iter_modules([str(package_dir)]):
        if name.startswith("_"):
            continue

        try:
            mod = importlib.import_module(f"modules.{name}")

            if not hasattr(mod, "TOOL_DEFINITIONS") or not hasattr(mod, "execute_tool"):
                logger.warning(f"Module '{name}' missing TOOL_DEFINITIONS or execute_tool — skipped")
                continue

            _modules[name] = mod

            for tool_def in mod.TOOL_DEFINITIONS:
                tool_name = tool_def["name"]
                if tool_name in _tool_dispatch:
                    existing_mod = _tool_dispatch[tool_name].__name__
                    logger.warning(
                        f"Tool name collision: '{tool_name}' in '{name}' overwrites '{existing_mod}'"
                    )
                _tool_definitions.append(tool_def)
                _tool_dispatch[tool_name] = mod

            logger.info(f"Loaded module: {name} ({len(mod.TOOL_DEFINITIONS)} tools)")

        except Exception:
            logger.exception(f"Failed to load module: {name}")

    # Verify no duplicate tool names after discovery
    tool_names = [t["name"] for t in _tool_definitions]
    assert len(tool_names) == len(set(tool_names)), \
        f"Duplicate tool names detected: {[n for n in tool_names if tool_names.count(n) > 1]}"

    logger.info(f"Discovery complete: {len(_modules)} modules, {len(_tool_definitions)} tools")


def get_tool_definitions() -> list[dict]:
    """All tool definitions from discovered modules."""
    return list(_tool_definitions)


async def execute_tool(name: str, input_data: dict) -> str | None:
    """Dispatch to the module that owns this tool. Returns None if not a module tool."""
    mod = _tool_dispatch.get(name)
    if mod is None:
        return None
    return await mod.execute_tool(name, input_data)


async def get_heartbeat_contributions() -> dict[str, str]:
    """Collect briefing data from all modules that provide it."""
    contributions = {}
    for name, mod in _modules.items():
        if not hasattr(mod, "heartbeat_contributions"):
            continue
        try:
            result = await mod.heartbeat_contributions()
            if result:
                contributions[name] = result
        except Exception:
            logger.exception(f"Heartbeat contribution failed: {name}")
    return contributions


def get_module_names() -> list[str]:
    """Names of all loaded modules."""
    return list(_modules.keys())
