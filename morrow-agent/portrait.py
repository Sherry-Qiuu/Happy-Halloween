"""Behavior-derived portrait: 从用户真实排布里推断的画像，和 sched_profile 的显式画像并存。

sched_profile 记录的是「用户自己说出来的」和「模型在带原话引用时才敢写的」，
这一层记录的是「从真实时间块 / 待办 / 目标里量出来的」，每次对话顺手更新一次，
所以演示里也能看到画像在长。

结果放在 profile 字典的 ``derived`` 字段，结构固定：

    {
      "updates": 3,                 # 更新过几次
      "updated_at": 1761900000.0,
      "samples": 12,                # 参与计算的条目数
      "block_minutes": 90,          # 常见时间块长度（分钟，取中位数）
      "blocks_per_day": 2.4,        # 每天排几块
      "day_start": "07:00",         # 最早开始
      "day_end": "24:00",           # 最晚结束
      "density": "normal",          # sparse / normal / dense
      "goal_count": 3,              # 在推进的目标数
      "estimate_bias": 1.15,        # 实际 / 计划 的用时比（有实际记录时）
      "completion": 0.4,            # 当日待办完成率
      "notes": ["习惯把长任务排在下午"]
    }
"""

from __future__ import annotations

import statistics
import time
from typing import Any


def _hours(start: Any) -> float | None:
    if isinstance(start, (int, float)):
        return float(start)
    if isinstance(start, str) and ":" in start:
        head, tail = start.split(":", 1)
        try:
            return int(head) + int(tail) / 60
        except ValueError:
            return None
    return None


def _clock(hours: float) -> str:
    whole = int(hours)
    minutes = int(round((hours - whole) * 60))
    return f"{whole:02d}:{minutes:02d}"


def _blocks(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = state.get("scheduled")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def derive(
    state: Any,
    previous: dict[str, Any] | None = None,
    feedback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Measure the portrait from the state the browser just sent."""
    if not isinstance(state, dict):
        state = {}
    prior = previous if isinstance(previous, dict) else {}

    blocks = _blocks(state)
    durations = [
        float(row.get("duration"))
        for row in blocks
        if isinstance(row.get("duration"), (int, float))
        and 5 <= float(row.get("duration")) <= 480
    ]
    starts = [
        hour
        for hour in (_hours(row.get("start")) for row in blocks)
        if hour is not None
    ]
    ends = [
        hour + (float(row.get("duration") or 0) / 60)
        for row, hour in zip(blocks, (_hours(row.get("start")) for row in blocks))
        if hour is not None
    ]

    days = state.get("schedule_days")
    day_count = len(days) if isinstance(days, list) and days else 1

    tasks = state.get("tasks") if isinstance(state.get("tasks"), list) else []
    done = sum(1 for task in tasks if isinstance(task, dict) and task.get("done"))
    completion = round(done / len(tasks), 2) if tasks else prior.get("completion")

    goals = state.get("goals") if isinstance(state.get("goals"), list) else []
    goal_count = len(goals)

    used = state.get("used") if isinstance(state.get("used"), list) else []
    planned_minutes = sum(
        float(row.get("duration") or 0) for row in blocks if isinstance(row, dict)
    )
    used_minutes = sum(
        float(row.get("duration") or 0) for row in used if isinstance(row, dict)
    )
    bias = None
    if planned_minutes > 0 and used_minutes > 0:
        bias = round(used_minutes / planned_minutes, 2)

    inferred: dict[str, Any] = {
        "samples": len(blocks) + len(tasks) + len(used),
        "block_minutes": int(statistics.median(durations)) if durations else None,
        "blocks_per_day": round(len(blocks) / max(1, day_count), 1),
        "day_start": _clock(min(starts)) if starts else None,
        "day_end": _clock(max(ends)) if ends else None,
        "goal_count": goal_count,
        "completion": completion,
    }
    if bias is not None:
        inferred["estimate_bias"] = bias

    per_day = inferred["blocks_per_day"]
    if per_day < 1.2:
        inferred["density"] = "sparse"
    elif per_day < 3.2:
        inferred["density"] = "normal"
    else:
        inferred["density"] = "dense"

    notes: list[str] = []
    if starts:
        morning = sum(1 for hour in starts if hour < 12)
        afternoon = sum(1 for hour in starts if 12 <= hour < 18)
        evening = sum(1 for hour in starts if hour >= 18)
        peak = max((morning, "上午"), (afternoon, "下午"), (evening, "晚上"))[1]
        notes.append(f"多数时间块排在{peak}")
    if inferred["block_minutes"] and inferred["block_minutes"] >= 90:
        notes.append("习惯用 90 分钟以上的长块")
    elif inferred["block_minutes"] and inferred["block_minutes"] <= 30:
        notes.append("倾向 30 分钟以内的小块")
    if bias is not None and bias >= 1.15:
        notes.append("实际投入常超过计划（估算偏乐观）")
    elif bias is not None and bias <= 0.85:
        notes.append("实际投入常少于计划（计划偏重）")

    # 人的决策：采纳 / 保留 / 改用策略引擎方案
    counts: dict[str, int] = {}
    if isinstance(feedback, dict) and isinstance(feedback.get("counts"), dict):
        for key, value in feedback["counts"].items():
            if isinstance(value, (int, float)):
                counts[str(key)] = int(value)
    if counts:
        accepted = counts.get("ai:accept", 0)
        dismissed = counts.get("ai:dismiss", 0)
        chosen = counts.get("planner:choose", 0)
        total = accepted + dismissed + chosen
        if total >= 3:
            if chosen and chosen / total >= 0.4:
                notes.append("更常采用策略引擎的方案，排程时优先给可执行的小步")
            elif dismissed and dismissed / max(1, accepted + dismissed) >= 0.5:
                notes.append("最近多份建议被保留，说明当下不想改计划，先问再做")
            elif accepted / max(1, accepted + dismissed) >= 0.7:
                notes.append("多数建议被直接采用，可以给更具体的方案")

    result = dict(inferred)
    result["notes"] = notes[:4]
    result["updated_at"] = time.time()
    result["updates"] = int(prior.get("updates") or 0) + 1
    if counts:
        result["feedback"] = {
            "accept": counts.get("ai:accept", 0),
            "dismiss": counts.get("ai:dismiss", 0),
            "choose_planner": counts.get("planner:choose", 0),
        }
    return result


def prompt_block(portrait: Any) -> str:
    """Compact text for the model; empty when nothing has been measured yet."""
    if not isinstance(portrait, dict) or not portrait.get("samples"):
        return ""
    lines: list[str] = []
    block = portrait.get("block_minutes")
    if block:
        lines.append(f"常见时间块：{block} 分钟")
    if portrait.get("blocks_per_day"):
        lines.append(f"每天排约 {portrait['blocks_per_day']} 块")
    if portrait.get("day_start") and portrait.get("day_end"):
        lines.append(f"作息区间：{portrait['day_start']}–{portrait['day_end']}")
    if portrait.get("density"):
        label = {"sparse": "偏稀疏", "normal": "正常", "dense": "偏密集"}.get(
            portrait["density"], portrait["density"]
        )
        lines.append(f"排布密度：{label}")
    if portrait.get("goal_count"):
        lines.append(f"在推进目标数：{portrait['goal_count']}")
    if portrait.get("completion") is not None:
        lines.append(f"当日待办完成率：{int(round(portrait['completion'] * 100))}%")
    if portrait.get("estimate_bias"):
        lines.append(f"实际/计划用时比：{portrait['estimate_bias']}")
    notes = portrait.get("notes") or []
    if notes:
        lines.append("观察到的习惯：" + "；".join(notes))
    lines.append(
        f"以上是从 {portrait.get('samples')} 条真实记录里量出来的，"
        "排时间时优先贴合这些习惯；和用户这次说的冲突时以用户为准。"
    )
    return "\n".join(lines)
