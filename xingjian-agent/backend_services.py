"""Standard-library planning services backed by calendar-rescheduler/core.py."""
from __future__ import annotations
import importlib.util
import sys
from datetime import date
from pathlib import Path
from typing import Any

_CORE = Path(__file__).resolve().parents[1] / "calendar-rescheduler" / "backend" / "core.py"
_spec = importlib.util.spec_from_file_location("calendar_rescheduler_core", _CORE)
_core = importlib.util.module_from_spec(_spec) if _spec else None
if _spec and _spec.loader and _core:
    # Register dynamically loaded modules before execution.  Dataclasses and
    # other introspection helpers resolve ``cls.__module__`` through
    # ``sys.modules`` while importing the planner core.
    sys.modules[_spec.name] = _core
    _spec.loader.exec_module(_core)


def _text(value: Any, default: str = "") -> str:
    return value.strip()[:500] if isinstance(value, str) else default


def decompose(payload: dict[str, Any]) -> dict[str, Any]:
    goal = _text(payload.get("goal") or payload.get("title"))
    if not goal:
        raise ValueError("goal 不能为空")
    count = payload.get("count", 4)
    try: count = max(1, min(8, int(count)))
    except (TypeError, ValueError): count = 4
    hints = payload.get("steps")
    if isinstance(hints, list):
        steps = [_text(x) for x in hints if _text(x)][:count]
    else:
        steps = []
    defaults = [f"明确「{goal}」的完成标准", f"收集完成「{goal}」所需的信息", f"完成「{goal}」的第一版", f"检查结果并补齐遗漏"]
    for item in defaults:
        if len(steps) >= count: break
        if item not in steps: steps.append(item)
    rows = [{"id": f"step-{i+1}", "title": s, "status": "todo", "order": i+1} for i, s in enumerate(steps)]
    return {"goal": goal, "steps": rows, "count": len(rows), "mode": "mock" if payload.get("mock") else "rule"}


def _event(raw: dict[str, Any], i: int):
    if not _core: raise RuntimeError("planner core unavailable")
    def integer(name, default):
        try: return int(raw.get(name, default))
        except (TypeError, ValueError): return default
    day = integer("day", 0)
    start = raw.get("start", 9 * 60)
    if isinstance(start, str) and ":" in start:
        h, m = start.split(":", 1); start = int(h) * 60 + int(m)
    else: start = integer("start", 9 * 60)
    return _core.Event(id=str(raw.get("id", f"event-{i+1}")), title=_text(raw.get("title"), f"事件 {i+1}"), day=max(0,min(6,day)), start=start, dur=max(5,integer("dur", integer("duration",30))), hardness=integer("hardness",3), priority=integer("priority",2), locked=bool(raw.get("locked",False)), kind=_text(raw.get("kind"),"task"), splittable=bool(raw.get("splittable",True)))


def replan(payload: dict[str, Any]) -> dict[str, Any]:
    if not _core: raise ValueError("planner core unavailable")
    raws = payload.get("events", [])
    if not isinstance(raws, list): raise ValueError("events 必须是数组")
    events = [_event(x, i) for i, x in enumerate(raws) if isinstance(x, dict)]
    d = payload.get("disruption") or {}
    if not isinstance(d, dict): raise ValueError("disruption 必须是对象")
    def integer(name, default=0):
        try: return int(d.get(name, default))
        except (TypeError, ValueError): return default
    disruption = _core.Disruption(kind=_text(d.get("kind"), "insert"), day=max(0,min(6,integer("day"))), start=integer("start", 14*60), dur=max(0,integer("dur", integer("duration",60))), title=_text(d.get("title"),"临时事项"), event_id=d.get("event_id"), hardness=integer("hardness",1))
    persona = _core.persona_for(
        _text(payload.get("persona"), "P2"),
        payload.get("scheduling_params") if isinstance(payload.get("scheduling_params"), dict) else None,
    )
    base = None
    raw_base = payload.get("base_date")
    if isinstance(raw_base, str) and raw_base.strip():
        try:
            base = date.fromisoformat(raw_base.strip())
        except ValueError:
            base = None
    result = _core.Planner(persona, base_date=base).propose(events, disruption).as_dict()
    result["mode"] = "mock" if payload.get("mock") else "core"
    return result


def reflection(payload: dict[str, Any]) -> dict[str, Any]:
    text = _text(payload.get("reflection") or payload.get("text"))
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    tasks = state.get("tasks") if isinstance(state.get("tasks"), list) else []
    done = sum(1 for x in tasks if isinstance(x, dict) and x.get("done"))
    facts = [f"记录了 {len(tasks)} 项待办，其中 {done} 项已完成。"]
    if text: facts.append(f"反思原文：{text[:120]}")
    return {"facts": facts, "patterns": ["临时事项会压缩可用于推进目标的连续时间（估算）。"], "questions": ["下一步最值得保留的是哪一件事？"], "source": "mock" if payload.get("mock") else "structured"}
