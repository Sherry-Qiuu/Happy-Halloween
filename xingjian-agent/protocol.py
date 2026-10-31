"""Wire protocol: server-sent events plus proposal validation.

The model is allowed to suggest changes to the user's plan, but it is
never trusted. :func:`sanitize_changes` is the only place where a
suggestion becomes structured data the browser is willing to apply.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

MAX_CHANGES = 4
MAX_TITLE = 60
MAX_GOAL = 30
MAX_TEXT = 80
MAX_DURATION = 240
MIN_DURATION = 5

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")

CHANGE_TYPES = (
    "move_event",
    "add_event",
    "remove_event",
    "add_task",
    "add_next_action",
    "log_actual",
)


def sse(event: str, payload: dict[str, Any] | str) -> bytes:
    """Encode one server-sent event."""
    body = payload if isinstance(payload, str) else json.dumps(
        payload, ensure_ascii=False
    )
    return f"event: {event}\ndata: {body}\n\n".encode("utf-8")


def _clean_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return text[:limit]


def normalize_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _DATE_RE.match(value.strip())
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def normalize_time(value: Any) -> str | None:
    """Return ``HH:MM`` for a 24h clock value, otherwise ``None``."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1]
    match = _TIME_RE.match(text)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def normalize_duration(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip().rstrip("m"))
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    if value != value:  # NaN
        return None
    minutes = int(round(value))
    if minutes < MIN_DURATION or minutes > MAX_DURATION:
        return None
    return minutes


def _clean_index(value: Any, goals_count: int | None) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = int(value.strip())
        except ValueError:
            return None
    if not isinstance(value, int):
        return None
    if value < 0 or (goals_count is not None and value >= goals_count):
        return None
    return value


def sanitize_change(
    raw: Any, goals_count: int | None = None
) -> dict[str, Any] | None:
    """Validate a single change and return a normalized copy."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    if kind not in CHANGE_TYPES:
        return None

    if kind in ("move_event", "add_event", "remove_event", "log_actual"):
        date = normalize_date(raw.get("date"))
        title = _clean_text(raw.get("title"), MAX_TITLE)
        if not date or not title:
            return None
        change: dict[str, Any] = {
            "type": kind,
            "date": date,
            "title": title,
        }
        if kind == "remove_event":
            return change
        start = normalize_time(raw.get("start"))
        duration = normalize_duration(raw.get("duration"))
        if not start or not duration:
            return None
        change["start"] = start
        change["duration"] = duration
        return change

    if kind == "add_task":
        title = _clean_text(raw.get("title"), MAX_TITLE)
        duration = normalize_duration(raw.get("minutes"))
        if not title or not duration:
            return None
        goal = _clean_text(raw.get("goal"), MAX_GOAL) or "临时安排"
        return {
            "type": kind,
            "title": title,
            "minutes": duration,
            "goal": goal,
        }

    if kind == "add_next_action":
        text = _clean_text(raw.get("text"), MAX_TEXT)
        duration = normalize_duration(raw.get("minutes"))
        index = _clean_index(raw.get("goal_index", 0), goals_count)
        if not text or not duration or index is None:
            return None
        return {
            "type": kind,
            "goal_index": index,
            "text": text,
            "minutes": duration,
        }

    return None


def sanitize_changes(
    raw: Any, goals_count: int | None = None
) -> list[dict[str, Any]]:
    """Validate a list of changes, dropping anything unusable."""
    if not isinstance(raw, list):
        return []
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        change = sanitize_change(item, goals_count)
        if not change:
            continue
        fingerprint = json.dumps(change, sort_keys=True)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        cleaned.append(change)
        if len(cleaned) >= MAX_CHANGES:
            break
    return cleaned


def summarize_change(change: dict[str, Any]) -> str:
    """Human readable one-liner for logs and fallback summaries."""
    kind = change["type"]
    if kind == "move_event":
        return (
            f"移动「{change['title']}」到 {change['date']} "
            f"{change['start']}（{change['duration']} 分钟）"
        )
    if kind == "add_event":
        return (
            f"新增日程「{change['title']}」{change['date']} "
            f"{change['start']}（{change['duration']} 分钟）"
        )
    if kind == "remove_event":
        return f"删除日程「{change['title']}」{change['date']}"
    if kind == "add_task":
        return f"新增待办「{change['title']}」（{change['minutes']} 分钟）"
    if kind == "add_next_action":
        return f"给目标添加下一步「{change['text']}」"
    return f"补记「{change['title']}」{change['duration']} 分钟"


def fallback_summary(changes: Iterable[dict[str, Any]]) -> str:
    parts = [summarize_change(change) for change in changes]
    return "；".join(parts)[:120]
