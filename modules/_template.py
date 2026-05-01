"""Module template — copy this to create a new GG capability module.

Required:
    TOOL_DEFINITIONS: list[dict]   — tool schemas (same format as agent.py)
    execute_tool(name, input_data)  — async, returns str

Optional:
    heartbeat_contributions()       — async, returns str or None for briefings
"""

TOOL_DEFINITIONS = [
    {
        "name": "example_tool",
        "description": "What this tool does.",
        "input_schema": {
            "type": "object",
            "properties": {
                "param": {
                    "type": "string",
                    "description": "What this param is for",
                },
            },
            "required": ["param"],
        },
    },
]


async def execute_tool(name: str, input_data: dict) -> str:
    """Dispatch tool calls for this module."""
    match name:
        case "example_tool":
            return f"Result: {input_data['param']}"
        case _:
            return f"Unknown tool: {name}"


async def heartbeat_contributions() -> str | None:
    """Return data to include in morning/evening briefings, or None."""
    return None
