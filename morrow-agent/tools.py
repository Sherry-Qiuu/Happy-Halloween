"""Deterministic tools the model may call during a turn.

Every function here is plain Python over the state document the browser
already sends.  The model never touches storage: it can only *ask* for a
fact or a computation, and the harness feeds the answer back.

Keeping this file free of LLM calls is deliberate — it is what makes the
assistant's answers checkable and testable.
"""

from __future__ import annotations

import re
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Any

WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
DAY_START = "07:00"
DAY_END = "23:00"
MAX_RANGE_DAYS = 14

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
              "八": 8, "九": 9, "十": 10}


class ToolError(ValueError):
    """Bad arguments from the model; the harness feeds the message back."""


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------

def _parse_day(value: str) -> date_cls:
    """Accept ISO dates, 今天/明天/后天, 周三/星期三, +3d."""
    text = (value or "").strip()
    if not text:
        raise ToolError("date 不能为空")
    if text in ("今天", "today", "今日"):
        return date_cls.today()
    if text in ("明天", "tomorrow"):
        return date_cls.today() + timedelta(days=1)
    if text in ("后天",):
        return date_cls.today() + timedelta(days=2)
    match = re.match(r"^\+?(\d{1,2})\s*(?:d|天)$", text)
    if match:
        return date_cls.today() + timedelta(days=int(match.group(1)))
    match = re.match(r"^(?:周|星期|礼拜)([一二三四五六日天1-7])$", text)
    if match:
        token = match.group(1)
        index = _CN_DIGITS.get(token)
        if index is None:
            index = int(token) if token.isdigit() else 1
        index = 7 if index == 0 or token in ("日", "天", "7") else index
        today = date_cls.today()
        ahead = (index - today.isoweekday()) % 7
        return today + timedelta(days=ahead)
    try:
        return date_cls.fromisoformat(text)
    except ValueError as error:
        raise ToolError(f"识别不了日期：{value}") from error


def _minutes(clock: str) -> int:
    try:
        hour, tail = str(clock).split(":", 1)
        return int(hour) * 60 + int(tail)
    except (ValueError, AttributeError) as error:
        raise ToolError(f"时间格式应为 HH:MM：{clock}") from error


def _clock(minutes: int) -> str:
    minutes = max(0, min(24 * 60, int(minutes)))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _weekday(day: date_cls) -> str:
    return WEEKDAYS[day.isoweekday() - 1]


# ---------------------------------------------------------------------------
# state readers
# ---------------------------------------------------------------------------

def _scheduled(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = state.get("scheduled")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("date")]


def _used(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = state.get("used")
    return [row for row in rows if isinstance(row, dict) and row.get("date")] if isinstance(rows, list) else []


def _tasks(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = state.get("tasks")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _blocks_on(state: dict[str, Any], day: date_cls) -> list[dict[str, Any]]:
    iso = day.isoformat()
    rows = []
    for row in _scheduled(state):
        if str(row.get("date")) != iso:
            continue
        start = _minutes(row.get("start") or "09:00")
        duration = int(row.get("duration") or 60)
        rows.append(
            {
                "title": row.get("title") or "未命名",
                "start": _clock(start),
                "end": _clock(start + duration),
                "duration": duration,
                "locked": bool(row.get("locked")),
            }
        )
    return sorted(rows, key=lambda item: item["start"])


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

def get_day(state: dict[str, Any], date: str = "today") -> dict[str, Any]:
    """Everything scheduled on one day, plus the tasks that span it."""
    day = _parse_day(date)
    iso = day.isoformat()
    blocks = _blocks_on(state, day)
    used = [
        {"title": row.get("title"), "start": row.get("start"), "duration": row.get("duration")}
        for row in _used(state)
        if str(row.get("date")) == iso
    ]
    tasks = [
        {
            "title": task.get("title"),
            "done": bool(task.get("done")),
            "minutes": task.get("minutes"),
            "goal": task.get("goal"),
        }
        for task in _tasks(state)
        if _task_touches(task, iso)
    ]
    busy = sum(int(item["duration"]) for item in blocks)
    return {
        "date": iso,
        "weekday": _weekday(day),
        "is_today": iso == date_cls.today().isoformat(),
        "blocks": blocks,
        "used": used,
        "tasks": tasks,
        "busy_minutes": busy,
        "free_minutes": max(0, _minutes(DAY_END) - _minutes(DAY_START) - busy),
    }


def _task_touches(task: dict[str, Any], iso: str) -> bool:
    start = str(task.get("start") or task.get("span_start") or "")
    end = str(task.get("end") or task.get("span_end") or "")
    if not start:
        return True
    return start <= iso <= (end or start)


def search_blocks(state: dict[str, Any], query: str, days: int = 7) -> dict[str, Any]:
    """Find scheduled blocks whose title contains *query*."""
    needle = (query or "").strip()
    if not needle:
        raise ToolError("query 不能为空")
    days = max(1, min(MAX_RANGE_DAYS, int(days or 7)))
    today = date_cls.today()
    horizon = {(today + timedelta(days=offset)).isoformat() for offset in range(days)}
    hits = []
    for row in _scheduled(state):
        iso = str(row.get("date"))
        if iso not in horizon:
            continue
        if needle.lower() in str(row.get("title") or "").lower():
            start = _minutes(row.get("start") or "09:00")
            duration = int(row.get("duration") or 60)
            hits.append(
                {
                    "title": row.get("title"),
                    "date": iso,
                    "weekday": _weekday(date_cls.fromisoformat(iso)),
                    "start": _clock(start),
                    "end": _clock(start + duration),
                    "duration": duration,
                    "locked": bool(row.get("locked")),
                }
            )
    return {"query": needle, "count": len(hits), "hits": sorted(hits, key=lambda item: (item["date"], item["start"]))}


def free_slots(
    state: dict[str, Any],
    date: str = "today",
    duration: int = 60,
    earliest: str = DAY_START,
    latest: str = DAY_END,
    count: int = 3,
) -> dict[str, Any]:
    """Gaps long enough for *duration* minutes, inside the day window."""
    day = _parse_day(date)
    duration = int(duration)
    if duration < 5 or duration > 480:
        raise ToolError("duration 应该在 5–480 分钟")
    count = max(1, min(8, int(count or 3)))
    lower = max(_minutes(earliest), _minutes(DAY_START))
    upper = min(_minutes(latest), _minutes(DAY_END))
    busy = sorted(
        (_minutes(item["start"]), _minutes(item["start"]) + int(item["duration"]))
        for item in _blocks_on(state, day)
    )
    slots: list[dict[str, str]] = []
    cursor = lower
    for start, end in busy + [(upper, upper)]:
        if start - cursor >= duration:
            slots.append({"start": _clock(cursor), "end": _clock(cursor + duration)})
            if len(slots) >= count:
                break
        cursor = max(cursor, end)
    return {
        "date": day.isoformat(),
        "weekday": _weekday(day),
        "duration": duration,
        "slots": slots,
        "note": "" if slots else "这个时段没有足够长的空档",
    }


def _planner_events(state: dict[str, Any], days: int) -> list[dict[str, Any]]:
    """把 3 天日程转成 rescheduler 的事件格式（day 偏移 + 分钟）。"""
    today = date_cls.today()
    horizon = {(today + timedelta(days=offset)).isoformat(): offset for offset in range(days)}
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(_scheduled(state)):
        iso = str(row.get("date"))
        if iso not in horizon:
            continue
        start = _minutes(row.get("start") or "09:00")
        rows.append(
            {
                "id": f"e{index}",
                "title": row.get("title") or "未命名",
                "day": horizon[iso],
                "start": start,
                "dur": int(row.get("duration") or 60),
                "hardness": 0 if row.get("locked") else 3,
                "kind": "meeting" if row.get("locked") else "focus",
                "locked": bool(row.get("locked")),
            }
        )
    return rows


def planner_replan(
    state: dict[str, Any],
    date: str,
    title: str,
    start: str,
    duration: int = 60,
    kind: str = "insert",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask the deterministic planner for a persona-aware proposal."""
    import backend_services

    day = _parse_day(date)
    offset = (day - date_cls.today()).days
    if not 0 <= offset <= 6:
        raise ToolError("planer 只看未来 7 天，换一个日期")
    events = _planner_events(state, 7)
    disruption = {
        "kind": kind if kind in ("insert", "switch", "overrun", "cancel", "illness") else "insert",
        "day": offset,
        "start": _minutes(start),
        "dur": int(duration),
        "title": (title or "临时事项")[:40],
        "hardness": 1,
    }
    payload: dict[str, Any] = {
        "events": events,
        "disruption": disruption,
        "base_date": date_cls.today().isoformat(),
    }
    if isinstance(params, dict) and params:
        payload["persona"] = "P2"
        payload["scheduling_params"] = params
    result = backend_services.replan(payload)
    return {
        "summary": result.get("summary"),
        "changes": result.get("changes") or [],
        "bill": result.get("bill") or {},
        "alternatives": (result.get("alternatives") or [])[:3],
        "warnings": result.get("warnings") or [],
        "tier": result.get("tier"),
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_day",
            "description": "读取某一天的日程块、实际记录和当天待办。用户提到“今天/明天/周三/11-05”时先查再答。",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "YYYY-MM-DD，或 今天/明天/后天/周三/+3d",
                    }
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_blocks",
            "description": "按标题关键词找未来几天里排在哪里，用于“把作品集挪一下”这类请求。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "标题关键词"},
                    "days": {"type": "integer", "minimum": 1, "maximum": 14},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "planner_replan",
            "description": "把一次变化交给排程引擎算方案：返回主方案、备选和影响账单（改动数/目标保留/留白）。涉及冲突、要挪已有安排、需要权衡时先调它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "YYYY-MM-DD 或 今天/明天/周三"},
                    "title": {"type": "string", "description": "新事件或要调整的事项标题"},
                    "start": {"type": "string", "description": "HH:MM 开始时间"},
                    "duration": {"type": "integer", "minimum": 5, "maximum": 480},
                    "kind": {
                        "type": "string",
                        "enum": ["insert", "switch", "overrun", "cancel", "illness"],
                        "description": "变化类型，默认 insert",
                    },
                },
                "required": ["date", "title", "start"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "free_slots",
            "description": "找某天里足够长的空档，用于安排新事情或挪动已有块。",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "duration": {"type": "integer", "minimum": 5, "maximum": 480},
                    "earliest": {"type": "string", "description": "HH:MM，默认 07:00"},
                    "latest": {"type": "string", "description": "HH:MM，默认 23:00"},
                    "count": {"type": "integer", "minimum": 1, "maximum": 8},
                },
                "required": ["date", "duration"],
            },
        },
    },
]

TOOL_IMPL = {
    "get_day": get_day,
    "search_blocks": search_blocks,
    "free_slots": free_slots,
    "planner_replan": planner_replan,
}


def call_tool(
    name: str,
    arguments: dict[str, Any],
    state: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> Any:
    """Run one tool call; raises ToolError for bad input."""
    if name not in TOOL_IMPL:
        raise ToolError(f"没有这个工具：{name}")
    if not isinstance(arguments, dict):
        raise ToolError("参数必须是 JSON 对象")
    args = dict(arguments)
    if name == "planner_replan" and isinstance(context, dict):
        params = context.get("scheduling_params")
        if isinstance(params, dict) and params:
            args.setdefault("params", params)
    try:
        return TOOL_IMPL[name](state, **args)
    except TypeError as error:
        raise ToolError(f"参数不对：{error}") from error
