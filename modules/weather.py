"""Weather module — OpenWeatherMap current-weather integration with location memory.

Tools:
    get_weather   — current conditions, temp °F, humidity, today's H/L

Heartbeat:
    One-line weather summary for the current remembered location.

Location memory:
    When a location is passed to get_weather it is persisted to a local
    file so that subsequent calls and heartbeat briefings automatically
    use it without the user repeating it.
"""

import json
import os
import logging
from pathlib import Path

import httpx
import tools

logger = logging.getLogger(__name__)

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
DEFAULT_LOCATION = os.getenv("DEFAULT_LOCATION", "New York, NY")
OWM_CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"
LOCATION_FILE = Path(__file__).parent.parent / ".current_location"


# ── helpers ───────────────────────────────────────────────────────────────────

async def _fetch_weather(location: str = "", lat: float | None = None, lon: float | None = None) -> dict:
    """Fetch current conditions from OWM. Uses lat/lon if available, otherwise city name."""
    params = {
        "appid": OPENWEATHER_API_KEY,
        "units": "imperial",
    }
    if lat is not None and lon is not None:
        params["lat"] = lat
        params["lon"] = lon
    else:
        params["q"] = location
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(OWM_CURRENT_URL, params=params)
        resp.raise_for_status()
        return resp.json()


def _format_weather(data: dict) -> str:
    """Build a human-readable multi-line weather report."""
    temp        = int(round(data["main"]["temp"]))
    feels_like  = int(round(data["main"]["feels_like"]))
    humidity    = data["main"]["humidity"]
    today_hi    = int(round(data["main"]["temp_max"]))
    today_lo    = int(round(data["main"]["temp_min"]))
    description = data["weather"][0]["description"].capitalize()
    city_name   = data["name"]
    country     = data["sys"]["country"]

    return (
        f"🌡️ Weather in {city_name}, {country}:\n"
        f"• Conditions: {description}\n"
        f"• Temperature: {temp}°F (feels like {feels_like}°F)\n"
        f"• Today's High / Low: {today_hi}°F / {today_lo}°F\n"
        f"• Humidity: {humidity}%"
    )


def _one_line_summary(data: dict) -> str:
    """Compact single-line summary for morning/evening briefings."""
    temp        = int(round(data["main"]["temp"]))
    today_hi    = int(round(data["main"]["temp_max"]))
    today_lo    = int(round(data["main"]["temp_min"]))
    description = data["weather"][0]["description"].capitalize()
    city_name   = data["name"]

    return (
        f"{city_name}: {description}, {temp}°F "
        f"(H {today_hi}° / L {today_lo}°)"
    )


def _save_location(location: str, lat: float | None = None, lon: float | None = None):
    """Persist current location to a simple file (survives restarts, no mem0 dedup issues)."""
    data = {"location": location}
    if lat is not None and lon is not None:
        data["lat"] = lat
        data["lon"] = lon
    LOCATION_FILE.write_text(json.dumps(data))
    logger.info("Updated the saved weather location")


def _load_location() -> dict:
    """Load current location from file. Returns {"location": str, "lat": float|None, "lon": float|None}."""
    try:
        if LOCATION_FILE.exists():
            data = json.loads(LOCATION_FILE.read_text())
            if data.get("location", "").strip():
                return {
                    "location": data["location"].strip(),
                    "lat": data.get("lat"),
                    "lon": data.get("lon"),
                }
    except Exception:
        logger.warning("Could not read location file, using default")
    return {"location": DEFAULT_LOCATION, "lat": None, "lon": None}


# ── tool definitions ──────────────────────────────────────────────────────────

async def update_location_from_gps(lat: float, lon: float) -> str:
    """Called from main.py when the user shares a Telegram location.
    Reverse-geocodes via OWM and saves the location.
    Returns a confirmation string.
    """
    if not OPENWEATHER_API_KEY:
        return "Weather unavailable: OPENWEATHER_API_KEY is not set."

    try:
        data = await _fetch_weather(lat=lat, lon=lon)
        city = data.get("name", "Unknown")
        country = data.get("sys", {}).get("country", "")
        location_name = f"{city}, {country}" if country else city
        _save_location(location_name, lat, lon)

        summary = _format_weather(data)
        return f"Location updated to {location_name}.\n\n{summary}"
    except Exception as exc:
        logger.warning("GPS weather lookup failed (%s)", type(exc).__name__)
        return "Could not look up that location. Check your weather API key and connection."


TOOL_DEFINITIONS = [
    {
        "name": "get_weather",
        "description": (
            "Get current weather for a location: temperature in °F, conditions, "
            "humidity, and today's high/low. "
            "When a location is supplied it is remembered so future requests "
            "(and morning briefings) automatically use it. "
            "Omit location to use the last known location (configured via DEFAULT_LOCATION)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": (
                        "City name, optionally with state/country "
                        "(e.g. 'Miami, FL', 'Tokyo, JP', 'London, UK'). "
                        "Leave blank to use the remembered location."
                    ),
                },
            },
            "required": [],
        },
    },
]


# ── tool execution ────────────────────────────────────────────────────────────

async def execute_tool(name: str, input_data: dict) -> str:
    """Dispatch tool calls for this module."""
    match name:
        case "get_weather":
            return await _handle_get_weather(input_data)
        case _:
            return f"Unknown tool: {name}"


async def _handle_get_weather(input_data: dict) -> str:
    location = (input_data.get("location") or "").strip()

    if not OPENWEATHER_API_KEY:
        return "Weather unavailable: OPENWEATHER_API_KEY is not set in the environment."

    lat, lon = None, None
    new_location = bool(location)
    if not new_location:
        saved = _load_location()
        location = saved["location"]
        lat, lon = saved.get("lat"), saved.get("lon")

    try:
        data = await _fetch_weather(location, lat, lon)
        if new_location:
            _save_location(location)
        return _format_weather(data)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            return "Weather unavailable: invalid OPENWEATHER_API_KEY — check your .env file."
        if exc.response.status_code == 404:
            return (
                f"Location not found: '{location}'. "
                "Try a more specific name, e.g. 'Miami, FL' or 'London, UK'."
            )
        return f"OpenWeatherMap returned HTTP {exc.response.status_code}."
    except httpx.RequestError as exc:
        logger.warning("Weather request failed (%s)", type(exc).__name__)
        return "Could not connect to OpenWeatherMap. Check the connection and try again."
    except Exception as exc:
        logger.warning("Weather lookup failed (%s)", type(exc).__name__)
        return "Could not read this weather report."


# ── heartbeat ─────────────────────────────────────────────────────────────────

async def heartbeat_contributions() -> str | None:
    """Return a one-line weather summary for inclusion in morning/evening briefings."""
    if not OPENWEATHER_API_KEY:
        return None

    try:
        saved = _load_location()
        data = await _fetch_weather(saved["location"], saved.get("lat"), saved.get("lon"))
        return _one_line_summary(data)
    except Exception as exc:
        logger.warning("Weather briefing lookup failed (%s)", type(exc).__name__)
        return None
