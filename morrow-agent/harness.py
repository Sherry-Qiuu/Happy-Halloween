"""Turn harness: a very small tool loop around the chat model.

Flow of one turn::

    model ──tool_calls?──▶ run tools ──▶ feed results ──▶ model …
        └──no calls──▶ parse <<<PROPOSAL>>> block ──▶ validate ──▶ done

Design rules, in order of importance:

1. the model may only *ask*; tools are deterministic Python (``tools.py``);
2. every turn ends with a structured block (proposal or clarify);
3. a proposal that breaks hard constraints goes back to the model once,
   then gets dropped — never applied;
4. the loop is bounded: ``MAX_STEPS`` model calls, ``MAX_TOOLS`` per step.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import deepseek
import deepseek_tools
import tools as tool_registry
from config import Settings

MAX_STEPS = 3
MAX_TOOLS_PER_STEP = 2
MAX_CHANGES = 12


class PlanError(ValueError):
    """A proposal that would break a hard constraint."""


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def _blocks(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = state.get("scheduled")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _minutes(clock: str) -> int:
    head, _, tail = str(clock).partition(":")
    return int(head or 0) * 60 + int(tail or 0)


def validate_plan(state: dict[str, Any], proposal: dict[str, Any]) -> list[str]:
    """Return the reasons this proposal must not be applied as-is."""
    issues: list[str] = []
    changes = proposal.get("changes") or []
    if not isinstance(changes, list) or not changes:
        return ["改动块里没有 changes"]
    if len(changes) > MAX_CHANGES:
        issues.append(f"改动 {len(changes)} 条，超过 {MAX_CHANGES} 条上限")

    existing = _blocks(state)
    locked_titles = {
        str(row.get("title")) for row in existing if row.get("locked")
    }
    by_key = {(str(row.get("date")), str(row.get("title"))): row for row in existing}

    for change in changes:
        if not isinstance(change, dict):
            issues.append("存在非对象的改动项")
            continue
        kind = change.get("type")
        if kind in ("move_event", "remove_event"):
            key = (str(change.get("date")), str(change.get("title")))
            if key not in by_key:
                issues.append(f"找不到要改的块：{change.get('date')} {change.get('title')}")
                continue
            if str(change.get("title")) in locked_titles:
                issues.append(f"「{change.get('title')}」是锁定项，不能移动或删除")
        if kind in ("add_event", "move_event", "log_actual"):
            duration = change.get("duration")
            if not isinstance(duration, int) or not 5 <= duration <= 480:
                issues.append(f"时长不在 5–480 分钟：{duration}")

    # 新增/移动后的时间段不能撞已有块（移动时排除它自己）
    placed: list[tuple[str, int, int, str]] = []
    for change in changes:
        if not isinstance(change, dict):
            continue
        if change.get("type") not in ("add_event", "move_event"):
            continue
        date = str(change.get("date"))
        start = _minutes(change.get("start") or "09:00")
        end = start + int(change.get("duration") or 60)
        title = str(change.get("title"))
        for row in existing:
            if str(row.get("date")) != date:
                continue
            if change.get("type") == "move_event" and str(row.get("title")) == title:
                continue
            other_start = _minutes(row.get("start") or "09:00")
            other_end = other_start + int(row.get("duration") or 60)
            if start < other_end and end > other_start:
                issues.append(
                    f"{date} {change.get('start')} 的「{title}」和已有「{row.get('title')}」冲突"
                )
        placed.append((date, start, end, title))

    for index, (date, start, end, title) in enumerate(placed):
        for other_date, other_start, other_end, other_title in placed[index + 1:]:
            if date == other_date and start < other_end and end > other_start:
                issues.append(f"同一次改动里「{title}」和「{other_title}」时段重叠")

    return issues


# ---------------------------------------------------------------------------
# cross-check: deterministic planner vs the model's own proposal
# ---------------------------------------------------------------------------

def _change_signature(changes: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for change in changes or []:
        if not isinstance(change, dict):
            continue
        kind = str(change.get("type"))
        if kind not in ("add_event", "move_event"):
            continue
        rows.append((kind, str(change.get("title")), str(change.get("start"))))
    return sorted(rows)


def cross_check(
    state: dict[str, Any],
    proposal: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Run the deterministic planner on the same disruption.

    Returns ``None`` when the planner cannot judge this proposal, otherwise
    a dict with the planner's own plan and whether the two agree.
    """
    changes = proposal.get("changes") or []
    target = None
    for change in changes:
        if isinstance(change, dict) and change.get("type") in ("add_event", "move_event"):
            target = change
            break
    if target is None:
        return None

    arguments = {
        "date": target.get("date"),
        "title": target.get("title") or "临时事项",
        "start": target.get("start") or "09:00",
        "duration": target.get("duration") or 60,
        "kind": "insert" if target.get("type") == "add_event" else "switch",
    }
    try:
        plan = tool_registry.call_tool(
            "planner_replan", arguments, state, context=context
        )
    except Exception:  # noqa: BLE001 - 交叉验证失败不能拖垮这一轮
        return None
    if not isinstance(plan, dict) or not plan.get("changes"):
        return None

    agrees = _change_signature(changes) == _change_signature(plan["changes"])
    return {
        "agrees": agrees,
        "summary": plan.get("summary") or "",
        "changes": plan.get("changes") or [],
        "bill": plan.get("bill") or {},
        "alternatives": plan.get("alternatives") or [],
        "warnings": plan.get("warnings") or [],
    }


def enrich(
    proposal: dict[str, Any] | None,
    state: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Attach the planner's bill and its alternative plan to a proposal."""
    if not isinstance(proposal, dict):
        return proposal
    check = cross_check(state, proposal, context)
    if check is None:
        return proposal
    enriched = dict(proposal)
    enriched["bill"] = check["bill"]
    if check["agrees"]:
        enriched["cross_check"] = {"agrees": True}
    else:
        enriched["cross_check"] = {
            "agrees": False,
            "summary": check["summary"],
            "changes": check["changes"],
            "bill": check["bill"],
            "alternatives": check["alternatives"],
        }
    return enriched


# ---------------------------------------------------------------------------
# deterministic fallback: text → disruption → planner plan
# ---------------------------------------------------------------------------

import re as _re
from datetime import date as _date, timedelta as _timedelta

_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def _cn_number(text: str) -> str:
    """把“十五 / 二十 / 两”这类数字换成阿拉伯数字，方便后面统一正则。"""
    def tens(match: "re.Match[str]") -> str:
        head, tail = match.group(1), match.group(2)
        value = (_CN_DIGITS.get(head, 1) if head else 1) * 10
        if tail:
            value += _CN_DIGITS.get(tail, 0)
        return str(value)

    out = _re.sub("([一二两三四五六七八九]?)十([一二三四五六七八九]?)", tens, text)
    for char, digit in _CN_DIGITS.items():
        out = out.replace(char, str(digit))
    return out


_PM_MARKERS = ("下午", "晚上", "傍晚", "夜里", "晚间")
_AM_MARKERS = ("上午", "早上", "凌晨", "清晨")
_HOUR_RE = _re.compile(r"(\d{1,2})\s*[点时]")
_RANGE_RE = _re.compile(r"(\d{1,2})\s*[点时]\s*(?:到|至|-|~|—)\s*(\d{1,2})\s*[点时]?")
_DURATION_RE = _re.compile(r"(\d{1,3})\s*分钟|(\d{1,2})\s*(?:个)?\s*小时")
_DAY_RE = _re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]")
_WEEKDAY_RE = _re.compile(r"(?:周|星期|礼拜)([一二三四五六日天1-7])")
_TITLE_HINTS = ("会议", "开会", "面试", "课", "讨论", "测评", "实验")


def parse_disruption(user_text: str) -> dict[str, Any] | None:
    """Best-effort parse of “几号几点做多久的什么事”。

    Returns ``None`` when the sentence is too vague to schedule; the
    caller then leaves the turn as a question.
    """
    raw = _re.sub(r"\s+", "", user_text or "")
    if not raw:
        return None
    clean = _cn_number(raw)

    day = None
    match = _DAY_RE.search(clean)
    if match:
        month, day_part = int(match.group(1)), int(match.group(2))
        today = _date.today()
        year = today.year + (1 if month < today.month else 0)
        try:
            day = _date(year, month, day_part)
        except ValueError:
            day = None
    if day is None:
        if any(word in clean for word in ("今天", "今晚", "今日")):
            day = _date.today()
        elif any(word in clean for word in ("明天", "明晚", "明早")):
            day = _date.today() + _timedelta(days=1)
        elif "后天" in clean:
            day = _date.today() + _timedelta(days=2)
        else:
            weekday_match = _WEEKDAY_RE.search(clean)
            if weekday_match:
                token = weekday_match.group(1)
                index = {"日": 7, "天": 7}.get(token) or int(token)
                ahead = (index - _date.today().isoweekday()) % 7
                day = _date.today() + _timedelta(days=ahead)
    if day is None:
        return None

    is_pm = any(mark in clean for mark in _PM_MARKERS)
    start_hour = None
    duration = None

    def _minutes_of(hour: int, half: bool) -> int:
        value = hour * 60 + (30 if half else 0)
        if is_pm and hour < 12 and value < 12 * 60:
            value += 12 * 60
        return value

    range_match = _RANGE_RE.search(clean)
    if range_match:
        start_text = clean[range_match.start(1):range_match.end(1)]
        end_text = clean[range_match.start(2):range_match.end(2)]
        start_half = bool(_re.search(r"点半", clean[range_match.end(1):range_match.end(1) + 3]))
        end_half = bool(_re.search(r"点半", clean[range_match.end(2):range_match.end(2) + 3]))
        start_value = _minutes_of(int(range_match.group(1)), start_half)
        end_value = _minutes_of(int(range_match.group(2)), end_half)
        if end_value <= start_value:
            end_value += 12 * 60
        start_hour = start_value // 60
        duration = end_value - start_value
    else:
        hour_match = _HOUR_RE.search(clean)
        if hour_match:
            half = bool(_re.search(r"点半", clean[hour_match.end(1):hour_match.end(1) + 3]))
            start_hour = _minutes_of(int(hour_match.group(1)), half) // 60

    if duration is None:
        len_match = _DURATION_RE.search(clean)
        if len_match and len_match.group(1):
            duration = int(len_match.group(1))
        elif len_match and len_match.group(2):
            duration = int(len_match.group(2)) * 60

    if start_hour is None or duration is None:
        return None
    if not (0 <= start_hour <= 23) or not 5 <= duration <= 480:
        return None

    title = "临时事项"
    for hint in _TITLE_HINTS:
        if hint in raw:
            title = "临时会议" if hint in ("会议", "开会") else hint
            break
    return {
        "date": day.isoformat(),
        "start": f"{start_hour:02d}:00",
        "duration": duration,
        "title": title,
    }


def fallback_plan(
    state: dict[str, Any],
    user_text: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """When the model wrote prose but no block, let the planner decide."""
    disruption = parse_disruption(user_text)
    if not disruption:
        return None
    try:
        plan = tool_registry.call_tool(
            "planner_replan", dict(disruption), state, context=context
        )
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(plan, dict) or not plan.get("changes"):
        return None
    return {
        "summary": plan.get("summary") or "策略引擎方案",
        "changes": plan["changes"],
        "bill": plan.get("bill") or {},
        "cross_check": {"agrees": True, "source": "planner"},
    }


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------

def _tool_message(call_id: str, payload: Any) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(payload, ensure_ascii=False)[:4000],
    }


def run_turn(
    settings: Settings,
    messages: list[dict[str, Any]],
    state: dict[str, Any],
    goals_count: int | None,
    *,
    write_event: Callable[[str, dict[str, Any]], bool] | None = None,
    log: Callable[[str], None] | None = None,
    context: dict[str, Any] | None = None,
    prefetched: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one user turn to completion; returns text + structured payload."""
    history: list[dict[str, Any]] = list(messages)
    emitted: list[str] = []
    proposal: dict[str, Any] | None = None
    clarify: dict[str, Any] | None = None
    persona: dict[str, Any] | None = None
    draft: dict[str, Any] | None = None
    used_tools: list[str] = []
    model_calls = 0

    def say(chunk: str) -> None:
        if not chunk:
            return
        emitted.append(chunk)
        if write_event is not None:
            write_event("delta", {"text": chunk})

    if prefetched and prefetched.get("changes"):
        history = list(history) + [
            {
                "role": "system",
                "content": (
                    "<planner_draft>排程引擎已经算好一版方案，直接用，不要重新搜索：\n"
                    + json.dumps(
                        {
                            "summary": prefetched.get("summary"),
                            "changes": prefetched.get("changes"),
                            "bill": prefetched.get("bill"),
                        },
                        ensure_ascii=False,
                    )
                    + "\n如果这份方案可行：正文用两句话说明关键取舍，然后原样输出同一个 changes。"
                    "如果你认为必须改动，只微调这一份（同一天、同一条），不要新增别的事。"
                    "</planner_draft>"
                ),
            }
        ]

    steps = 1 if (prefetched and prefetched.get("changes")) else MAX_STEPS
    tools_for_turn = None if (prefetched and prefetched.get("changes")) else tool_registry.TOOL_SCHEMAS

    for step in range(steps):
        model_calls += 1
        splitter = deepseek.ProposalSplitter()
        turn: dict[str, Any] = {"content": "", "tool_calls": [], "finish_reason": ""}
        for kind, value in deepseek_tools.iter_events(
            settings, history, tools_for_turn
        ):
            if kind == "delta":
                safe = deepseek.strip_state_tags(splitter.feed(value))
                if safe:
                    say(safe)
            else:
                turn = value
        tail = deepseek.strip_state_tags(splitter.finish())
        if tail:
            say(tail)

        calls = turn.get("tool_calls") or []
        if calls and step < MAX_STEPS - 1:
            history.append(
                {
                    "role": "assistant",
                    "content": turn.get("content") or "",
                    "tool_calls": calls[:MAX_TOOLS_PER_STEP],
                }
            )
            for call in calls[:MAX_TOOLS_PER_STEP]:
                name = (call.get("function") or {}).get("name") or ""
                raw_args = (call.get("function") or {}).get("arguments") or "{}"
                try:
                    arguments = json.loads(raw_args) if raw_args.strip() else {}
                except json.JSONDecodeError:
                    arguments = {}
                try:
                    result = tool_registry.call_tool(
                        name, arguments, state, context=context
                    )
                except tool_registry.ToolError as error:
                    result = {"error": str(error)}
                used_tools.append(name)
                if log:
                    log(f"[tool] {name} {json.dumps(arguments, ensure_ascii=False)[:120]}")
                history.append(_tool_message(call.get("id") or name, result))
            continue

        proposal, persona, draft, clarify = deepseek.parse_turn(
            splitter.block, goals_count
        )
        if proposal:
            issues = validate_plan(state, proposal)
            if issues and step < MAX_STEPS - 1:
                history.append({"role": "assistant", "content": turn.get("content") or ""})
                history.append(
                    {
                        "role": "user",
                        "content": "这份改动不能直接采用，原因：" + "；".join(issues[:4])
                        + "。请修正后重新输出改动块，不要解释。",
                    }
                )
                proposal = None
                continue
            if issues:
                proposal = None
        break

    if not proposal and not clarify and prefetched and prefetched.get("changes"):
        proposal = {
            "summary": prefetched.get("summary") or "排程引擎方案",
            "changes": prefetched["changes"],
            "bill": prefetched.get("bill") or {},
            "cross_check": {"agrees": True, "source": "planner"},
        }

    check = None
    if proposal:
        proposal = enrich(proposal, state, context)
        check = (proposal or {}).get("cross_check")

    return {
        "text": "".join(emitted),
        "proposal": proposal,
        "clarify": clarify,
        "persona": persona,
        "draft": draft,
        "tools": used_tools,
        "model_calls": model_calls,
        "cross_check": check,
    }
