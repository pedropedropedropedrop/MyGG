"""Whoop module — sleep, recovery, strain, workouts and profile from Whoop's developer API.

Tools:
    get_whoop_recovery   — recovery score, HRV, RHR (today, optional `days` trend)
    get_whoop_sleep      — last night's sleep performance, stages, efficiency
    get_whoop_strain     — day strain, kilojoules burned, avg/max HR
    get_whoop_workouts   — recent workouts with sport, strain, HR zones
    get_whoop_profile    — name, height, weight, max HR

Heartbeat:
    One-line summary: recovery + sleep + yesterday's strain. Skipped silently
    if .whoop_token.json is absent or refresh fails.

OAuth:
    The token cache lives at .whoop_token.json (gitignored). It is created by
    `whoop_auth.py` and refreshed in-place by _refresh_token below.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from constants import (
    WHOOP_API_BASE,
    WHOOP_API_TIMEOUT_SECONDS,
    WHOOP_DEFAULT_DAYS,
    WHOOP_DEFAULT_WORKOUTS,
    WHOOP_MAX_DAYS_LOOKBACK,
    WHOOP_MAX_WORKOUTS,
    WHOOP_OAUTH_TOKEN_URL,
    WHOOP_REFRESH_RETRY_LIMIT,
    WHOOP_TOKEN_REFRESH_SKEW_SECONDS,
)

logger = logging.getLogger(__name__)

TOKEN_FILE = Path(__file__).parent.parent / ".whoop_token.json"

# Whoop recovery color bands (from their app UX). Tuples are (lower-bound %, label).
_RECOVERY_BANDS = [
    (67, "green"),
    (34, "yellow"),
    (0,  "red"),
]

# Common Whoop sport_id → display name. Falls back to "Sport {id}" if unknown.
_SPORT_NAMES = {
    -1: "Activity",
    0: "Running",
    1: "Cycling",
    16: "Baseball",
    17: "Basketball",
    18: "Rowing",
    19: "Fencing",
    20: "Field Hockey",
    21: "Football",
    22: "Golf",
    24: "Ice Hockey",
    25: "Lacrosse",
    27: "Rugby",
    28: "Sailing",
    29: "Skiing",
    30: "Soccer",
    31: "Softball",
    32: "Squash",
    33: "Swimming",
    34: "Tennis",
    35: "Track & Field",
    36: "Volleyball",
    37: "Water Polo",
    38: "Wrestling",
    39: "Boxing",
    42: "Dance",
    43: "Pilates",
    44: "Yoga",
    45: "Weightlifting",
    47: "Cross Country Skiing",
    48: "Functional Fitness",
    52: "Hiking/Rucking",
    62: "HIIT",
    63: "Spin",
    65: "Meditation",
    66: "Other",
    71: "Surfing",
    73: "Elliptical",
    80: "Stair Climber",
    96: "Pickleball",
    97: "Snowboarding",
    98: "Walking",
}


# ── token management ─────────────────────────────────────────────────────────

def _load_token() -> dict | None:
    """Load the cached OAuth token from disk, or None if missing/corrupt."""
    if not TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_FILE.read_text())
        assert "access_token" in data, "token file missing access_token"
        return data
    except Exception:
        logger.exception("Failed to read Whoop token file")
        return None


def _save_token(token: dict) -> None:
    """Persist the token dict back to disk."""
    assert "access_token" in token, "cannot persist token without access_token"
    TOKEN_FILE.write_text(json.dumps(token, indent=2))


def _is_expired(token: dict) -> bool:
    """True if the token is past expiry (with a small safety skew)."""
    expires_at = float(token.get("expires_at", 0))
    return time.time() + WHOOP_TOKEN_REFRESH_SKEW_SECONDS >= expires_at


async def _refresh_token(token: dict) -> dict:
    """Exchange refresh_token for a fresh access_token. Persists and returns the new dict."""
    refresh_token = token.get("refresh_token", "")
    assert refresh_token, "no refresh_token available; re-run whoop_auth.py"
    client_id = os.getenv("WHOOP_CLIENT_ID", "").strip()
    client_secret = os.getenv("WHOOP_CLIENT_SECRET", "").strip()
    assert client_id and client_secret, "WHOOP_CLIENT_ID/SECRET missing in environment"

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": token.get("scope", ""),
    }
    async with httpx.AsyncClient(timeout=WHOOP_API_TIMEOUT_SECONDS) as client:
        resp = await client.post(WHOOP_OAUTH_TOKEN_URL, data=payload)
        resp.raise_for_status()
        body = resp.json()

    new_token = {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", refresh_token),
        "expires_at": time.time() + int(body.get("expires_in", 0)),
        "scope": body.get("scope", token.get("scope", "")),
        "token_type": body.get("token_type", "bearer"),
    }
    _save_token(new_token)
    logger.info("Refreshed Whoop access token")
    return new_token


# ── HTTP layer ───────────────────────────────────────────────────────────────

class WhoopNotConnectedError(RuntimeError):
    """Raised when no token cache is present — user needs to run whoop_auth.py."""


async def _authed_request(method: str, path: str, params: dict | None = None) -> dict:
    """Call the Whoop API with bearer auth, refreshing once on 401."""
    assert path.startswith("/"), "path must start with '/'"
    token = _load_token()
    if token is None:
        raise WhoopNotConnectedError(
            "Whoop is not connected. Run `python whoop_auth.py` once to grant consent."
        )
    if _is_expired(token):
        token = await _refresh_token(token)

    url = f"{WHOOP_API_BASE}{path}"
    attempts = 0
    last_error = None
    # Bounded retry loop: at most one refresh + retry on 401 (NASA Rule 1).
    while attempts <= WHOOP_REFRESH_RETRY_LIMIT:
        headers = {"Authorization": f"Bearer {token['access_token']}"}
        async with httpx.AsyncClient(timeout=WHOOP_API_TIMEOUT_SECONDS) as client:
            resp = await client.request(method, url, params=params, headers=headers)
        if resp.status_code == 401 and attempts < WHOOP_REFRESH_RETRY_LIMIT:
            logger.info("Whoop 401 — refreshing token and retrying once")
            token = await _refresh_token(token)
            attempts += 1
            continue
        if resp.status_code >= 400:
            last_error = f"Whoop API {resp.status_code}: {resp.text[:200]}"
            break
        return resp.json()
    raise RuntimeError(last_error or "Whoop API request failed after refresh retry")


# ── helpers ──────────────────────────────────────────────────────────────────

def _clamp_days(days: int | None) -> int:
    """Clamp the user-supplied lookback window into [1, WHOOP_MAX_DAYS_LOOKBACK]."""
    n = int(days) if days else WHOOP_DEFAULT_DAYS
    if n < 1:
        n = 1
    if n > WHOOP_MAX_DAYS_LOOKBACK:
        n = WHOOP_MAX_DAYS_LOOKBACK
    return n


def _window(days: int) -> dict:
    """Build start/end ISO 8601 query params covering the last `days` days."""
    assert 1 <= days <= WHOOP_MAX_DAYS_LOOKBACK, "days out of range"
    end = datetime.now(timezone.utc) + timedelta(hours=1)
    start = end - timedelta(days=days, hours=1)
    return {
        "start": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "limit": 25,
    }


def _ms_to_hm(ms: float | int) -> str:
    """Format milliseconds as 'Xh Ym'."""
    total_min = int(round(float(ms) / 60000.0))
    return f"{total_min // 60}h {total_min % 60:02d}m"


def _kj_to_kcal(kj: float) -> int:
    """Convert kilojoules to kilocalories (1 kcal ≈ 4.184 kJ)."""
    return int(round(float(kj) / 4.184))


def _recovery_band(score: float) -> str:
    """Map a numeric recovery score to Whoop's color band label."""
    for floor, label in _RECOVERY_BANDS:
        if score >= floor:
            return label
    return "red"


def _local_date(iso_ts: str) -> str:
    """Format an ISO 8601 UTC timestamp as a local-time short date+time."""
    if not iso_ts:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%a %b %d, %H:%M")
    except Exception:
        return iso_ts


def _records(payload: dict) -> list[dict]:
    """Pull the records list out of a paginated Whoop response."""
    if isinstance(payload, dict):
        return list(payload.get("records") or [])
    return []


def _scored(record: dict) -> dict | None:
    """Return the record's `score` dict if it has been scored, else None."""
    if record.get("score_state") != "SCORED":
        return None
    score = record.get("score")
    return score if isinstance(score, dict) else None


def _sport_name(record: dict) -> str:
    """Best-effort sport label from a workout record."""
    name = record.get("sport_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    sid = record.get("sport_id")
    if isinstance(sid, int):
        return _SPORT_NAMES.get(sid, f"Sport {sid}")
    return "Activity"


# ── fetchers ─────────────────────────────────────────────────────────────────

async def _fetch_recovery(days: int) -> list[dict]:
    payload = await _authed_request("GET", "/v2/recovery", params=_window(days))
    return _records(payload)


async def _fetch_sleep(days: int) -> list[dict]:
    payload = await _authed_request("GET", "/v2/activity/sleep", params=_window(days))
    return _records(payload)


async def _fetch_cycles(days: int) -> list[dict]:
    payload = await _authed_request("GET", "/v2/cycle", params=_window(days))
    return _records(payload)


async def _fetch_workouts(days: int) -> list[dict]:
    payload = await _authed_request("GET", "/v2/activity/workout", params=_window(days))
    return _records(payload)


async def _fetch_profile() -> dict:
    return await _authed_request("GET", "/v2/user/profile/basic")


async def _fetch_body() -> dict:
    return await _authed_request("GET", "/v2/user/measurement/body")


# ── formatters ───────────────────────────────────────────────────────────────

def _format_recovery_line(record: dict) -> str:
    score = _scored(record)
    when = _local_date(record.get("created_at", ""))
    if not score:
        return f"• {when}: not yet scored"
    rec = float(score.get("recovery_score") or 0)
    hrv = float(score.get("hrv_rmssd_milli") or 0)
    rhr = float(score.get("resting_heart_rate") or 0)
    return (
        f"• {when}: {int(round(rec))}% ({_recovery_band(rec)}) — "
        f"HRV {hrv:.0f}ms · RHR {rhr:.0f} bpm"
    )


def _format_recovery(records: list[dict]) -> str:
    if not records:
        return "No recovery data in that window."
    lines = ["Recovery:"]
    # Most recent first.
    sorted_recs = sorted(records, key=lambda r: r.get("created_at", ""), reverse=True)
    for rec in sorted_recs:
        lines.append(_format_recovery_line(rec))
    return "\n".join(lines)


def _format_sleep_record(record: dict) -> str:
    score = _scored(record)
    when = _local_date(record.get("start", ""))
    nap = " (nap)" if record.get("nap") else ""
    if not score:
        return f"• {when}{nap}: not yet scored"
    stages = score.get("stage_summary") or {}
    in_bed = _ms_to_hm(stages.get("total_in_bed_time_milli") or 0)
    light = _ms_to_hm(stages.get("total_light_sleep_time_milli") or 0)
    deep = _ms_to_hm(stages.get("total_slow_wave_sleep_time_milli") or 0)
    rem = _ms_to_hm(stages.get("total_rem_sleep_time_milli") or 0)
    perf = float(score.get("sleep_performance_percentage") or 0)
    eff = float(score.get("sleep_efficiency_percentage") or 0)
    disturbances = stages.get("disturbance_count") or 0
    return (
        f"• {when}{nap}: {perf:.0f}% performance, {eff:.0f}% efficiency\n"
        f"   In bed {in_bed} · REM {rem} · Deep {deep} · Light {light}\n"
        f"   Disturbances: {disturbances}"
    )


def _format_sleep(records: list[dict]) -> str:
    if not records:
        return "No sleep data in that window."
    lines = ["Sleep:"]
    sorted_recs = sorted(records, key=lambda r: r.get("start", ""), reverse=True)
    for rec in sorted_recs:
        lines.append(_format_sleep_record(rec))
    return "\n".join(lines)


def _format_strain_record(record: dict) -> str:
    score = _scored(record)
    when = _local_date(record.get("start", ""))
    if not score:
        return f"• {when}: not yet scored"
    strain = float(score.get("strain") or 0)
    kj = float(score.get("kilojoule") or 0)
    avg_hr = score.get("average_heart_rate") or 0
    max_hr = score.get("max_heart_rate") or 0
    return (
        f"• {when}: strain {strain:.1f} · "
        f"{int(round(kj))} kJ (~{_kj_to_kcal(kj)} kcal) · "
        f"avg {avg_hr} / max {max_hr} bpm"
    )


def _format_strain(records: list[dict]) -> str:
    if not records:
        return "No strain data in that window."
    lines = ["Strain (calories burned):"]
    sorted_recs = sorted(records, key=lambda r: r.get("start", ""), reverse=True)
    for rec in sorted_recs:
        lines.append(_format_strain_record(rec))
    return "\n".join(lines)


def _format_workout(record: dict) -> str:
    score = _scored(record)
    sport = _sport_name(record)
    when = _local_date(record.get("start", ""))
    if not score:
        return f"• {when} · {sport}: not yet scored"
    strain = float(score.get("strain") or 0)
    kj = float(score.get("kilojoule") or 0)
    avg_hr = score.get("average_heart_rate") or 0
    max_hr = score.get("max_heart_rate") or 0
    distance_m = score.get("distance_meter")
    extras = []
    if isinstance(distance_m, (int, float)) and distance_m > 0:
        extras.append(f"{distance_m / 1000:.2f} km")
    extras_str = (" · " + " · ".join(extras)) if extras else ""
    return (
        f"• {when} · {sport}: strain {strain:.1f} · "
        f"{int(round(kj))} kJ (~{_kj_to_kcal(kj)} kcal) · "
        f"avg {avg_hr} / max {max_hr} bpm{extras_str}"
    )


def _format_workouts(records: list[dict], limit: int) -> str:
    if not records:
        return "No workouts in that window."
    sorted_recs = sorted(records, key=lambda r: r.get("start", ""), reverse=True)
    lines = ["Workouts:"]
    for rec in sorted_recs[:limit]:
        lines.append(_format_workout(rec))
    return "\n".join(lines)


def _format_profile(profile: dict, body: dict) -> str:
    name = profile.get("first_name") or "Unknown"
    last = profile.get("last_name") or ""
    height_m = body.get("height_meter")
    weight_kg = body.get("weight_kilogram")
    max_hr = body.get("max_heart_rate")
    parts = [f"Name: {name} {last}".strip()]
    if isinstance(height_m, (int, float)):
        parts.append(f"Height: {height_m:.2f} m")
    if isinstance(weight_kg, (int, float)):
        parts.append(f"Weight: {weight_kg:.1f} kg")
    if isinstance(max_hr, (int, float)):
        parts.append(f"Max HR: {int(max_hr)} bpm")
    return "\n".join(parts)


# ── tool definitions ─────────────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "get_whoop_recovery",
        "description": (
            "Get the user's Whoop recovery score (0-100, color-banded), HRV (ms) and "
            "resting heart rate (bpm). Defaults to today; pass `days` (1-7) for a trend. "
            "Use this when the user asks how recovered they are, their readiness, HRV, or RHR."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "How many days back to include. Default 1 (today). Max 7.",
                    "minimum": 1,
                    "maximum": WHOOP_MAX_DAYS_LOOKBACK,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_whoop_sleep",
        "description": (
            "Get the user's Whoop sleep performance: time in bed, sleep stages "
            "(REM / deep / light), efficiency %, disturbances. Defaults to last night; "
            "pass `days` (1-7) for a trend. Use when the user asks about sleep quality, "
            "sleep score, REM, deep sleep, or how well they slept."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "How many nights back to include. Default 1. Max 7.",
                    "minimum": 1,
                    "maximum": WHOOP_MAX_DAYS_LOOKBACK,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_whoop_strain",
        "description": (
            "Get the user's Whoop day strain (0-21), kilojoules burned (calories OUT), "
            "and average / max heart rate. NOTE: this is calories BURNED, not eaten. "
            "Defaults to today; pass `days` (1-7) for a trend."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "How many days back to include. Default 1. Max 7.",
                    "minimum": 1,
                    "maximum": WHOOP_MAX_DAYS_LOOKBACK,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_whoop_workouts",
        "description": (
            "List the user's recent Whoop-tracked workouts with sport, time, strain, "
            "kJ burned, and avg/max HR. Default last 3, max 10. Use when the user asks "
            "about their recent training or sessions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max workouts to return. Default 3. Max 10.",
                    "minimum": 1,
                    "maximum": WHOOP_MAX_WORKOUTS,
                },
                "days": {
                    "type": "integer",
                    "description": "Window in days to search for workouts. Default 7.",
                    "minimum": 1,
                    "maximum": WHOOP_MAX_DAYS_LOOKBACK,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_whoop_profile",
        "description": (
            "Get the user's Whoop profile basics: first name, height, weight, and max "
            "heart rate. Useful for any tool that needs body composition context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


# ── tool execution ───────────────────────────────────────────────────────────

async def execute_tool(name: str, input_data: dict) -> str:
    """Dispatch tool calls for this module."""
    try:
        match name:
            case "get_whoop_recovery":
                return await _handle_recovery(input_data)
            case "get_whoop_sleep":
                return await _handle_sleep(input_data)
            case "get_whoop_strain":
                return await _handle_strain(input_data)
            case "get_whoop_workouts":
                return await _handle_workouts(input_data)
            case "get_whoop_profile":
                return await _handle_profile()
            case _:
                return f"Unknown tool: {name}"
    except WhoopNotConnectedError as exc:
        return str(exc)
    except httpx.HTTPStatusError as exc:
        logger.exception("Whoop HTTP error")
        return f"Whoop API error {exc.response.status_code}: {exc.response.text[:200]}"
    except httpx.RequestError as exc:
        logger.exception("Whoop network error")
        return f"Network error talking to Whoop: {exc}"
    except Exception as exc:
        logger.exception("Whoop tool failed")
        return f"Whoop tool failed: {exc}"


async def _handle_recovery(input_data: dict) -> str:
    days = _clamp_days(input_data.get("days"))
    records = await _fetch_recovery(days)
    return _format_recovery(records)


async def _handle_sleep(input_data: dict) -> str:
    days = _clamp_days(input_data.get("days"))
    records = await _fetch_sleep(days)
    return _format_sleep(records)


async def _handle_strain(input_data: dict) -> str:
    days = _clamp_days(input_data.get("days"))
    records = await _fetch_cycles(days)
    return _format_strain(records)


async def _handle_workouts(input_data: dict) -> str:
    days = _clamp_days(input_data.get("days") or WHOOP_MAX_DAYS_LOOKBACK)
    raw_limit = input_data.get("limit") or WHOOP_DEFAULT_WORKOUTS
    limit = max(1, min(int(raw_limit), WHOOP_MAX_WORKOUTS))
    records = await _fetch_workouts(days)
    return _format_workouts(records, limit)


async def _handle_profile() -> str:
    profile = await _fetch_profile()
    body = await _fetch_body()
    return _format_profile(profile, body)


# ── heartbeat ────────────────────────────────────────────────────────────────

def _summarize_recovery(records: list[dict]) -> str | None:
    if not records:
        return None
    latest = max(records, key=lambda r: r.get("created_at", ""))
    score = _scored(latest)
    if not score:
        return None
    rec = float(score.get("recovery_score") or 0)
    return f"Recovery {int(round(rec))}% ({_recovery_band(rec)})"


def _summarize_sleep(records: list[dict]) -> str | None:
    if not records:
        return None
    latest = max(records, key=lambda r: r.get("start", ""))
    score = _scored(latest)
    if not score:
        return None
    stages = score.get("stage_summary") or {}
    in_bed = _ms_to_hm(stages.get("total_in_bed_time_milli") or 0)
    perf = float(score.get("sleep_performance_percentage") or 0)
    return f"Sleep {in_bed} ({perf:.0f}%)"


def _summarize_strain(records: list[dict]) -> str | None:
    if not records:
        return None
    # "Yesterday's" strain: take the most-recent fully scored cycle that is not today.
    sorted_recs = sorted(records, key=lambda r: r.get("start", ""), reverse=True)
    for rec in sorted_recs:
        score = _scored(rec)
        if score:
            strain = float(score.get("strain") or 0)
            return f"strain {strain:.1f}"
    return None


async def heartbeat_contributions() -> str | None:
    """One-line Whoop summary for morning/evening briefings."""
    if _load_token() is None:
        return None
    try:
        recovery = await _fetch_recovery(2)
        sleep = await _fetch_sleep(2)
        cycles = await _fetch_cycles(2)
    except Exception:
        logger.exception("Whoop heartbeat fetch failed")
        return None

    parts = [p for p in (
        _summarize_recovery(recovery),
        _summarize_sleep(sleep),
        _summarize_strain(cycles),
    ) if p]
    if not parts:
        return None
    return "Whoop: " + " · ".join(parts)
