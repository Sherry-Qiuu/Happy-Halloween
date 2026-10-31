# -*- coding: utf-8 -*-
"""Benchmark the rescheduler on synthetic personas and disruptions.

This is a fast sanity harness, not a user study.  It answers three questions:
  1. does a plan come back in milliseconds?
  2. does the persona-weighted planner reduce expected user rejection?
  3. do the proposals stay inside the 4-change protocol and hard constraints?
"""

from __future__ import annotations

import json
import random
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from core import (Disruption, Event, GENERIC, PERSONAS, Planner,
                  PersonaPolicy, persona_for)

BASE = date(2026, 11, 2)


@dataclass
class Scenario:
    key: str
    label: str
    classes: int
    meetings: int
    focus: int
    social: int
    seed: int


SCENARIOS = [
    Scenario("freshman", "P8", 7, 2, 3, 2, 1),
    Scenario("kaoyan", "P1", 2, 1, 6, 0, 2),
    Scenario("club", "P5", 4, 6, 3, 3, 3),
    Scenario("intern", "P5", 5, 4, 4, 1, 4),
    Scenario("adhd", "P7", 4, 2, 3, 0, 5),
    Scenario("perfection", "P3", 4, 3, 4, 1, 6),
    Scenario("timezone", "P2", 3, 5, 4, 1, 7),
    Scenario("caregiver", "P4", 3, 2, 3, 0, 8),
    Scenario("athlete", "P2", 3, 2, 4, 1, 9),
    Scenario("freelance", "P5", 1, 5, 5, 2, 10),
    Scenario("lowdata", "P8", 4, 2, 3, 1, 11),
    Scenario("examweek", "P1", 6, 1, 5, 0, 12),
]


def _free(events: list[Event], day: int, start: int, dur: int) -> bool:
    for e in events:
        if e.day == day and max(start, e.start) < min(start + dur, e.end):
            return False
    return True


def build_schedule(s: Scenario) -> list[Event]:
    rng = random.Random(s.seed)
    events: list[Event] = []
    counter = 0

    def add(day, start, dur, title, hardness, kind, energy="deep", notify=False,
            locked=False):
        nonlocal counter
        if day > 6 or start < 8 * 60 or start + dur > 21 * 60:
            return
        if not _free(events, day, start, dur):
            return
        events.append(Event(id=f"e{counter}", title=title, day=day, start=start, dur=dur,
                            hardness=hardness, kind=kind, energy=energy, notify=notify,
                            locked=locked, splittable=kind in ("focus", "task", "habit")))
        counter += 1

    # a hard, locked exam creates a real "cannot move" constraint
    add(2, 9 * 60, 120, "期中考试", 0, "meeting", "deep", True, locked=True)
    # fixed weekly anchors, intentionally dense
    for d in range(5):
        add(d, 9 * 60, 90, "专业课", 1, "class", "medium", True)
        add(d, 11 * 60, 60, "例会", 1, "meeting", "medium", True)
        add(d, 12 * 60, 60, "午饭", 2, "routine", "low")
        add(d, 14 * 60, 90, "课程", 1, "class", "medium", True)
        add(d, 16 * 60, 120, "专注块", 3, "focus", "deep")
        add(d, 18 * 60 + 30, 60, "晚饭", 2, "routine", "low")
        add(d, 20 * 60, 30, "晚间阅读", 4, "habit", "low")
    for i in range(s.classes):
        add(i % 5, 14 * 60, 90, f"课程{i+1}", 1, "class", "medium", True)
    for i in range(s.meetings):
        add((i + 1) % 5, 15 * 60, 60, f"会议{i+1}", 1, "meeting", "medium", True)
    for i in range(s.focus):
        add((i * 2) % 7, 16 * 60, 90, "专注块", 3, "focus", "deep")
    for i in range(s.social):
        add((i * 3) % 6, 19 * 60, 90, "朋友约饭", 2, "social", "medium", True)
    # one deadline task and one habit
    add(2, 17 * 60, 60, "课程作业", 3, "task", "deep")
    add(4, 20 * 60, 30, "晚间阅读", 4, "habit", "low")
    return events


def disruptions(events: list[Event], n: int = 80) -> list[Disruption]:
    rng = random.Random(20261101)
    out: list[Disruption] = []
    for i in range(n):
        if i % 3 == 0:
            base = rng.choice(events)
            start = max(8 * 60, base.start - rng.choice((0, 15, 30)))
            out.append(Disruption("insert", base.day, start=start,
                                  dur=rng.choice((90, 120, 180)),
                                  title=f"临时会议{i}"))
        elif i % 3 == 1:
            candidates = [e for e in events if e.kind in ("meeting", "class")]
            target = rng.choice(candidates)
            out.append(Disruption("overrun", target.day, dur=rng.choice((15, 30, 45)),
                                  event_id=target.id))
        else:
            out.append(Disruption("illness", rng.randrange(0, 5)))
    return out


def _sigmoid(x: float) -> float:
    import math
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def score_for_user(plan, user: PersonaPolicy) -> tuple[float, float]:
    """Score a plan with the *user's* policy, not the planner's policy."""
    prompt_pen = {"silent": 0.1, "toast": 0.35, "preview": 0.8, "digest": 0.45}.get(plan.tier, 0.4)
    slack_pen = max(0.0, (user.slack_reserve - plan.slack_minutes) / max(1, user.slack_reserve))
    capacity_pen = min(1.0, max(0.0, plan.load_ratio - user.daily_ratio) / max(0.05, 1 - user.daily_ratio))
    breath_pen = min(1.0, plan.breathing_loss / max(1, user.break_minutes * 2))
    breath_reward = min(1.0, plan.breathing_minutes / max(1, user.break_minutes * 2))
    u = (user.goal_w * plan.goal
         - user.change_w * min(1.0, plan.changes / max(1, user.max_changes))
         - user.social_w * plan.social
         - user.energy_w * min(1.0, plan.energy_mismatch)
         - user.debt_w * min(1.0, plan.debt / 2.0)
         - user.continuity_w * min(1.0, plan.continuity)
         - user.prompt_w * prompt_pen
         - user.slack_w * slack_pen
         - user.capacity_w * capacity_pen
         - user.breathing_w * breath_pen
         + user.breathing_w * 0.8 * breath_reward)
    z = (1.0 + user.accept_bias
         - 0.60 * plan.changes
         - 1.00 * plan.social
         - 0.50 * min(1.0, plan.energy_mismatch)
         - 0.80 * plan.conflict
         + 1.20 * plan.goal
         - 0.35 * capacity_pen
         - 0.25 * slack_pen
         - 0.20 * breath_pen
         + 0.20 * breath_reward)
    p = max(0.05, min(0.95, _sigmoid(z)))
    return p, p * u - (1 - p) * 0.8 * plan.conflict


def run(planner: Planner, events: list[Event], dis: list[Disruption],
        user: PersonaPolicy) -> list[dict]:
    rows = []
    for d in dis:
        p = planner.propose(events, d)
        user_p, user_exp = score_for_user(p.plan, user)
        rows.append({
            "latency": p.latency_ms,
            "changes": p.plan.changes,
            "goal": p.plan.goal,
            "social": p.plan.social,
            "conflict": p.plan.conflict,
            "p_accept": user_p,
            "expected": user_exp,
            "planner_p": p.plan.p_accept,
            "conflict_left": 1 if p.plan.conflict > 0 else 0,
            "infeasible": 1 if p.infeasible else 0,
            "protocol_ok": 1 if all(c["type"] in ("move_event", "add_event", "remove_event")
                                   for c in p.changes) and len(p.changes) <= 4 else 0,
        })
    return rows


def main() -> None:
    all_rows: dict[str, list[dict]] = {"generic": [], "persona": []}
    by_scenario: dict[str, dict[str, list[dict]]] = {}
    for s in SCENARIOS:
        events = build_schedule(s)
        dis = disruptions(events, 80)
        label = s.label
        user = persona_for(label)
        plans = {
            "generic": Planner(GENERIC, base_date=BASE),
            "persona": Planner(persona_for(label), base_date=BASE),
        }
        for name, planner in plans.items():
            rows = run(planner, events, dis, user)
            all_rows[name].extend(rows)
            by_scenario.setdefault(s.key, {})[name] = rows

    print(f"{'strategy':<10}{'p50ms':>8}{'p95ms':>8}{'chg':>6}{'goal':>6}"
          f"{'social':>8}{'p_acc':>8}{'exp':>8}{'conf':>7}{'infes':>7}")
    for name in ("generic", "persona"):
        rows = all_rows[name]
        lat = sorted(r["latency"] for r in rows)
        print(f"{name:<10}{statistics.median(lat):>8.1f}{lat[int(len(lat)*0.95)]:>8.1f}"
              f"{statistics.mean(r['changes'] for r in rows):>6.2f}"
              f"{statistics.mean(r['goal'] for r in rows):>6.2f}"
              f"{statistics.mean(r['social'] for r in rows):>8.2f}"
              f"{statistics.mean(r['p_accept'] for r in rows):>8.3f}"
              f"{statistics.mean(r['expected'] for r in rows):>8.3f}"
              f"{statistics.mean(r['conflict_left'] for r in rows):>7.2f}"
              f"{statistics.mean(r['infeasible'] for r in rows):>7.2f}")

    print("\nper scenario: expected utility (generic -> persona)")
    for s in SCENARIOS:
        g = statistics.mean(r["expected"] for r in by_scenario[s.key]["generic"])
        p = statistics.mean(r["expected"] for r in by_scenario[s.key]["persona"])
        acc = statistics.mean(r["p_accept"] for r in by_scenario[s.key]["persona"])
        print(f"{s.key:<12} {g:>7.3f} -> {p:>7.3f}   p_accept={acc:.2f}  label={s.label}")

    # save a compact artifact for the report
    with open("out_benchmark.json", "w", encoding="utf-8") as f:
        json.dump({k: {
            "n": len(v),
            "p50_ms": statistics.median(r["latency"] for r in v),
            "p95_ms": sorted(r["latency"] for r in v)[int(len(v)*0.95)],
            "changes": statistics.mean(r["changes"] for r in v),
            "goal": statistics.mean(r["goal"] for r in v),
            "social": statistics.mean(r["social"] for r in v),
            "p_accept": statistics.mean(r["p_accept"] for r in v),
            "expected": statistics.mean(r["expected"] for r in v),
            "conflict_left": statistics.mean(r["conflict_left"] for r in v),
            "infeasible": statistics.mean(r["infeasible"] for r in v),
            "protocol_ok": statistics.mean(r["protocol_ok"] for r in v),
        } for k, v in all_rows.items()}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
