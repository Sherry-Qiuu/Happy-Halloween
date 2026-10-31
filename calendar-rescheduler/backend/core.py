# -*- coding: utf-8 -*-
"""Fast persona-aware calendar rescheduling strategy.

The module is intentionally self-contained: it does not call an LLM and does
not depend on the server.  A caller passes the next 7 days of events, one
disruption, and a persona policy; it gets back a proposal with a small number
of protocol-compatible changes, a cost bill, alternatives, and an explanation.

Design goals, in order:
  1. never violate a hard constraint (locked / H0 / time boundary);
  2. change as few events as possible;
  3. preserve the user's goal minutes;
  4. respect the persona's change / continuity / social / energy weights;
  5. leave breathing room and day-level slack for ad-hoc plans;
  6. answer in milliseconds, not by searching the whole month.
"""

from __future__ import annotations

import itertools
import math
import time
from copy import copy
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

DAY_MINUTES = 24 * 60
WORK_START = 8 * 60
WORK_END = 22 * 60
MAX_CHANGES = 4
EPS = 1e-9


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class Event:
    id: str
    title: str
    day: int                 # 0..6, 0 = base date
    start: int               # minutes from midnight
    dur: int                 # minutes
    hardness: int = 3        # H0..H5
    priority: int = 2        # P1..P4
    locked: bool = False
    kind: str = "focus"      # class / meeting / focus / task / habit / social / break
    splittable: bool = True
    min_chunk: int = 15
    deadline: int | None = None
    energy: str = "deep"     # low / medium / deep
    stackable: bool = False
    notify: bool = False
    source: str = "self"

    @property
    def end(self) -> int:
        return self.start + self.dur

    def clone(self) -> "Event":
        return copy(self)


@dataclass
class Disruption:
    """One change in the world that may require a replan."""

    kind: str                # insert / switch / overrun / cancel / illness / external
    day: int
    start: int = 0
    dur: int = 0
    title: str = "临时会议"
    event_id: str | None = None
    hardness: int = 1
    notify: bool = True


@dataclass
class Move:
    event_id: str
    to_day: int
    to_start: int
    to_dur: int
    reason: str = ""


@dataclass
class Plan:
    kind: str
    moves: list[Move] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    stacked: list[tuple[str, str]] = field(default_factory=list)
    added: Event | None = None
    goal: float = 1.0
    social: float = 0.0
    conflict: int = 0
    changes: int = 0
    energy_mismatch: float = 0.0
    debt: float = 0.0
    continuity: float = 0.0
    tier: str = "silent"
    utility: float = 0.0
    p_accept: float = 0.5
    expected: float = 0.0
    cross_day: bool = False
    explanation: list[str] = field(default_factory=list)
    slack_minutes: int = 0
    load_ratio: float = 0.0
    breathing_minutes: int = 0
    breathing_loss: int = 0
    capacity_violation: float = 0.0


@dataclass
class Proposal:
    summary: str
    changes: list[dict[str, Any]]
    plan: Plan
    alternatives: list[dict[str, Any]]
    bill: dict[str, Any]
    tier: str
    explanation: list[str]
    warnings: list[str]
    infeasible: bool
    latency_ms: float
    deferred: int = 0
    manual: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "changes": self.changes,
            "tier": self.tier,
            "bill": self.bill,
            "explanation": self.explanation,
            "warnings": self.warnings,
            "infeasible": self.infeasible,
            "alternatives": self.alternatives,
            "latency_ms": round(self.latency_ms, 3),
            "deferred": self.deferred,
            "manual": self.manual,
        }


# ---------------------------------------------------------------------------
# Persona policies
# ---------------------------------------------------------------------------

@dataclass
class PersonaPolicy:
    label: str
    goal_w: float = 0.8
    change_w: float = 0.6
    continuity_w: float = 0.5
    social_w: float = 0.8
    energy_w: float = 0.5
    debt_w: float = 0.6
    prompt_w: float = 0.4
    accept_bias: float = 0.0
    max_changes: int = MAX_CHANGES
    block_minutes: int = 60
    buffer_minutes: int = 10
    avoid_after: int = 21 * 60
    radius_days: int = 3
    stack_allowed: bool = False
    daily_ratio: float = 1.0
    # breathing / slack policy
    slack_reserve: int = 60          # free minutes to keep on a day
    breathing_anchors: tuple[tuple[int, int], ...] = (
        (12 * 60, 15), (15 * 60 + 30, 10), (17 * 60 + 30, 10)
    )
    break_minutes: int = 15
    break_after_deep: int = 90
    slack_w: float = 0.7
    capacity_w: float = 1.2
    breathing_w: float = 0.8


# Numbers are defaults for the eight portraits in 用户画像类型与排布策略.md.
# They are deliberately coarse: the point is relative ordering, not precision.
PERSONAS: dict[str, PersonaPolicy] = {
    "P1": PersonaPolicy("P1", goal_w=1.10, change_w=0.20, continuity_w=0.30,
                        social_w=0.40, energy_w=0.30, debt_w=0.35, prompt_w=0.30,
                        accept_bias=0.10, block_minutes=75, buffer_minutes=10,
                        radius_days=4, daily_ratio=0.82, slack_reserve=45,
                        breathing_anchors=((12 * 60, 30), (15 * 60 + 30, 10), (17 * 60 + 30, 10)),
                        break_minutes=10, break_after_deep=120,
                        slack_w=0.5, capacity_w=1.5, breathing_w=0.5),
    "P2": PersonaPolicy("P2", goal_w=0.55, change_w=1.00, continuity_w=1.00,
                        social_w=1.10, energy_w=0.50, debt_w=0.70, prompt_w=0.50,
                        accept_bias=-0.10, block_minutes=25, buffer_minutes=5,
                        radius_days=2, daily_ratio=0.70, slack_reserve=75,
                        breathing_anchors=((12 * 60, 45), (15 * 60 + 30, 15), (17 * 60 + 30, 15)),
                        break_minutes=15, break_after_deep=90,
                        slack_w=1.0, capacity_w=1.2, breathing_w=1.0),
    "P3": PersonaPolicy("P3", goal_w=0.80, change_w=0.50, continuity_w=0.40,
                        social_w=0.50, energy_w=0.65, debt_w=0.80, prompt_w=0.35,
                        accept_bias=0.00, block_minutes=40, buffer_minutes=15,
                        radius_days=3, daily_ratio=0.72, slack_reserve=75,
                        breathing_anchors=((12 * 60, 30), (15 * 60 + 30, 15), (17 * 60 + 30, 15)),
                        break_minutes=15, break_after_deep=90,
                        slack_w=0.8, capacity_w=1.2, breathing_w=0.8),
    "P4": PersonaPolicy("P4", goal_w=0.65, change_w=0.40, continuity_w=0.60,
                        social_w=0.50, energy_w=0.45, debt_w=0.50, prompt_w=0.40,
                        accept_bias=0.05, block_minutes=40, buffer_minutes=10,
                        radius_days=3, daily_ratio=0.62, slack_reserve=90,
                        breathing_anchors=((12 * 60, 45), (15 * 60 + 30, 20), (17 * 60 + 30, 15)),
                        break_minutes=20, break_after_deep=90,
                        slack_w=1.0, capacity_w=1.3, breathing_w=1.0),
    "P5": PersonaPolicy("P5", goal_w=0.75, change_w=0.40, continuity_w=0.30,
                        social_w=0.50, energy_w=0.75, debt_w=0.60, prompt_w=0.40,
                        accept_bias=0.00, block_minutes=30, buffer_minutes=10,
                        radius_days=2, daily_ratio=0.68, slack_reserve=60,
                        breathing_anchors=((10 * 60 + 30, 10), (12 * 60 + 30, 30), (15 * 60 + 30, 10)),
                        break_minutes=10, break_after_deep=60,
                        slack_w=0.7, capacity_w=1.1, breathing_w=0.7),
    "P6": PersonaPolicy("P6", goal_w=0.80, change_w=0.50, continuity_w=0.50,
                        social_w=0.70, energy_w=0.50, debt_w=0.60, prompt_w=0.50,
                        accept_bias=-0.05, block_minutes=45, buffer_minutes=10,
                        radius_days=3, daily_ratio=0.65, slack_reserve=75,
                        breathing_anchors=((12 * 60, 30), (15 * 60 + 30, 15), (17 * 60 + 30, 15)),
                        break_minutes=15, break_after_deep=90,
                        slack_w=0.9, capacity_w=1.2, breathing_w=0.9),
    "P7": PersonaPolicy("P7", goal_w=0.70, change_w=0.55, continuity_w=0.60,
                        social_w=0.40, energy_w=0.50, debt_w=0.70, prompt_w=0.30,
                        accept_bias=0.10, block_minutes=15, buffer_minutes=10,
                        radius_days=2, stack_allowed=True, daily_ratio=0.70,
                        slack_reserve=60,
                        breathing_anchors=((10 * 60 + 30, 10), (15 * 60 + 30, 10), (17 * 60 + 30, 10)),
                        break_minutes=10, break_after_deep=60,
                        slack_w=0.8, capacity_w=1.1, breathing_w=0.8),
    "P8": PersonaPolicy("P8", goal_w=0.60, change_w=0.45, continuity_w=0.50,
                        social_w=0.50, energy_w=0.40, debt_w=0.50, prompt_w=0.20,
                        accept_bias=0.00, block_minutes=30, buffer_minutes=10,
                        radius_days=2, daily_ratio=0.65, slack_reserve=75,
                        breathing_anchors=((12 * 60, 30), (15 * 60 + 30, 15), (17 * 60 + 30, 15)),
                        break_minutes=15, break_after_deep=90,
                        slack_w=0.8, capacity_w=1.1, breathing_w=0.8),
}

GENERIC = PersonaPolicy("generic", goal_w=0.80, change_w=0.15, continuity_w=0.15,
                        social_w=0.10, energy_w=0.10, debt_w=0.20, prompt_w=0.10,
                        accept_bias=0.30, block_minutes=60, buffer_minutes=10,
                        radius_days=7, stack_allowed=True, daily_ratio=0.78,
                        slack_reserve=60, break_minutes=10,
                        slack_w=0.5, capacity_w=1.0, breathing_w=0.5)


def persona_for(label: str | None, params: dict[str, Any] | None = None) -> PersonaPolicy:
    """Build a policy from a P1-P8 label and optional sched_profile params."""
    base = PERSONAS.get((label or "P2").upper(), PERSONAS["P2"])
    policy = copy(base)
    if not params:
        return policy
    try:
        if params.get("block_minutes"):
            policy.block_minutes = int(params["block_minutes"])
        if params.get("buffer_minutes"):
            policy.buffer_minutes = int(params["buffer_minutes"])
        if params.get("avoid_after"):
            avoid = params["avoid_after"]
            if isinstance(avoid, (list, tuple)):
                avoid = avoid[-1] if avoid else None
            if isinstance(avoid, str) and ":" in avoid:
                h, m = avoid.split(":", 1)
                policy.avoid_after = int(h) * 60 + int(m)
            elif isinstance(avoid, (int, float)):
                policy.avoid_after = int(avoid) * 60 if avoid <= 24 else int(avoid)
        if params.get("estimate_bias"):
            bias = float(params["estimate_bias"])
            # a person who overruns needs more buffer and a lower daily load
            policy.buffer_minutes = max(policy.buffer_minutes, int(10 + (bias - 1) * 20))
            policy.daily_ratio = max(0.6, min(1.0, 1.3 - bias * 0.4))
    except (TypeError, ValueError):
        pass
    return policy


# ---------------------------------------------------------------------------
# Core planner
# ---------------------------------------------------------------------------

def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _fmt(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h:02d}:{m:02d}"


def _peak(start: int) -> bool:
    h = start / 60.0
    return 9 <= h < 12 or 14 <= h < 18


def _overlap(a: Event, b: Event) -> bool:
    return a.day == b.day and max(a.start, b.start) < min(a.end, b.end)


# ---------------------------------------------------------------------------
# Breathing room / slack
# ---------------------------------------------------------------------------

def _window(persona: PersonaPolicy) -> tuple[int, int]:
    return WORK_START, max(WORK_START, min(WORK_END, persona.avoid_after))


def _merged_intervals(events: Iterable[Event], day: int, lo: int, hi: int,
                      include_breaks: bool = True) -> list[tuple[int, int]]:
    rows: list[tuple[int, int]] = []
    for e in events:
        if e.day != day or e.end <= lo or e.start >= hi:
            continue
        if not include_breaks and e.kind == "break":
            continue
        rows.append((max(lo, e.start), min(hi, e.end)))
    rows.sort()
    merged: list[list[int]] = []
    for s, t in rows:
        if not merged or s > merged[-1][1]:
            merged.append([s, t])
        else:
            merged[-1][1] = max(merged[-1][1], t)
    return [(s, t) for s, t in merged]


def _covered(merged: list[tuple[int, int]]) -> int:
    return sum(t - s for s, t in merged)


def day_metrics(events: Iterable[Event], day: int, persona: PersonaPolicy) -> dict[str, int | float]:
    """Work load, free slack, and visible breathing minutes for one day."""
    lo, hi = _window(persona)
    window = max(0, hi - lo)
    work = _merged_intervals(events, day, lo, hi, include_breaks=False)
    all_busy = _merged_intervals(events, day, lo, hi, include_breaks=True)
    break_minutes = sum(e.dur for e in events if e.day == day and e.kind == "break")
    free = max(0, window - _covered(all_busy))
    load = (_covered(work) / window) if window else 0.0
    return {
        "window": window,
        "work_minutes": _covered(work),
        "free_minutes": free,
        "breathing_minutes": break_minutes,
        "load_ratio": load,
        "slack_minutes": free,
    }


def _find_break_slot(events: list[Event], day: int, anchor: int, dur: int,
                     persona: PersonaPolicy) -> int | None:
    lo, hi = _window(persona)
    anchor = max(lo, min(hi - dur, anchor))
    offsets = [0]
    for step in range(5, 61, 5):
        offsets.extend((-step, step))
    for delta in offsets:
        start = anchor + delta
        if start < lo or start + dur > hi:
            continue
        if any(e.day == day and max(start, e.start) < min(start + dur, e.end)
               for e in events):
            continue
        if any(e.day == day and e.kind == "break" and abs(e.start - start) < 20
               for e in events):
            continue
        return start
    return None


def ensure_breathing(events: list[Event], persona: PersonaPolicy) -> list[Event]:
    """Insert visible rest blocks into a plan without overlapping work.

    Called before :class:`Planner`; the planner then treats them as normal
    events, but its score penalises moving or shrinking them.
    """
    out = [e.clone() for e in events]
    counter = 0
    for day in range(7):
        work = [e for e in out if e.day == day and e.kind != "break"]
        if not work:
            continue
        added_today = 0
        for anchor, dur in persona.breathing_anchors:
            if added_today >= 4:
                break
            if any(e.day == day and e.kind == "break" and abs(e.start - anchor) < 45
                   for e in out):
                continue
            if day_metrics(out, day, persona)["slack_minutes"] - dur < persona.slack_reserve:
                continue
            start = _find_break_slot(out, day, anchor, dur, persona)
            if start is None:
                continue
            out.append(Event(
                id=f"br{counter}", title="呼吸 · 休息", day=day, start=start,
                dur=dur, hardness=4, priority=3, kind="break",
                splittable=True, min_chunk=10, energy="low",
            ))
            counter += 1
            added_today += 1
        # after a long deep block, add a short recovery break if there is room
        for e in list(out):
            if added_today >= 4:
                break
            if e.day != day or e.kind not in ("focus", "task"):
                continue
            if e.dur < persona.break_after_deep:
                continue
            if any(b.day == day and b.kind == "break" and abs(b.start - e.end) < 45
                   for b in out):
                continue
            if day_metrics(out, day, persona)["slack_minutes"] - persona.break_minutes < persona.slack_reserve:
                continue
            start = _find_break_slot(out, day, e.end + 5, persona.break_minutes, persona)
            if start is None:
                continue
            out.append(Event(
                id=f"br{counter}", title="呼吸 · 休息", day=day, start=start,
                dur=persona.break_minutes, hardness=4, priority=3, kind="break",
                splittable=True, min_chunk=10, energy="low",
            ))
            counter += 1
            added_today += 1
    return out


class Planner:
    def __init__(self, persona: PersonaPolicy, state: dict[str, Any] | None = None,
                 base_date: date | None = None):
        self.persona = persona
        self.state = state or {}
        self.base_date = base_date or date(2026, 11, 2)

    # -- public ---------------------------------------------------------
    def propose(self, events: list[Event], disruption: Disruption) -> Proposal:
        t0 = time.perf_counter()
        events = [e.clone() for e in events]
        added: Event | None = None
        conflicts: list[Event] = []

        if disruption.kind in ("insert", "switch"):
            added = Event(
                id="new",
                title=disruption.title,
                day=disruption.day,
                start=disruption.start,
                dur=disruption.dur,
                hardness=disruption.hardness,
                priority=1,
                kind="meeting" if disruption.kind == "insert" else "focus",
                splittable=False,
                energy="medium" if disruption.kind == "insert" else "deep",
                notify=disruption.notify,
                source="external" if disruption.kind == "insert" else "self",
            )
            events.append(added)
            conflicts = self._conflicts_of(events, added)

        elif disruption.kind in ("overrun", "external"):
            target = self._by_id(events, disruption.event_id)
            if target is None:
                return self._empty_proposal(t0, "找不到要调整的事件")
            if disruption.kind == "overrun":
                target.dur += disruption.dur
            else:
                target.day = disruption.day
                target.start = disruption.start
                target.dur = disruption.dur or target.dur
            conflicts = self._conflicts_of(events, target)

        elif disruption.kind == "cancel":
            target = self._by_id(events, disruption.event_id)
            if target is None:
                return self._empty_proposal(t0, "找不到要取消的事件")
            events = [e for e in events if e.id != target.id]
            plan = Plan(kind="cancel", goal=1.0, changes=1,
                        explanation=[f"「{target.title}」取消，空出的时间先不自动填满。"])
            self._apply_metrics(plan, events)
            return self._finish(plan, events, None, t0, conflicts=0)

        elif disruption.kind == "illness":
            conflicts = [e for e in events
                         if e.day == disruption.day and e.kind in ("focus", "task")]
            if not conflicts:
                return self._empty_proposal(t0, "今天没有需要顺延的专注或任务")
        else:
            return self._empty_proposal(t0, f"未知的变化类型：{disruption.kind}")

        if not conflicts:
            plan = Plan(
                kind="add" if added else "keep",
                added=added,
                goal=1.0,
                changes=1 if added else 0,
                explanation=["没有冲突，直接写入。" if added else "没有冲突。"],
            )
            self._apply_metrics(plan, events)
            return self._finish(plan, events, added, t0, conflicts=0)

        plans = self._candidate_plans(events, conflicts, added, disruption)
        best, alternatives, infeasible = self._select(plans, conflicts)
        return self._finish(best, events, added, t0,
                            conflicts=len(conflicts), alternatives=alternatives,
                            infeasible=infeasible)

    # -- candidate generation ------------------------------------------
    def _candidate_plans(self, events: list[Event], conflicts: list[Event],
                         added: Event | None, disruption: Disruption) -> list[Plan]:
        per_victim: list[list[Plan]] = []
        for victim in conflicts:
            per_victim.append(self._options_for(victim, events, disruption))

        # keep-at-least-one option per victim; cap the product so latency stays flat
        plans: list[Plan] = []
        for combo in itertools.product(*per_victim):
            plans.append(self._combine(combo, added, events))
            if len(plans) >= 24:
                break
        return plans or [self._combine([self._keep(conflicts[0])], added, events)]

    def _options_for(self, victim: Event, events: list[Event],
                     disruption: Disruption) -> list[Plan]:
        opts: list[Plan] = [self._keep(victim)]
        if victim.locked or victim.hardness == 0:
            return opts

        # moves: top two slots that do not blow the day's capacity
        moves = [p for p in self._move_options(victim, events)
                 if self._capacity_ok(p, victim, events)]
        opts.extend(moves[:2])

        # shorten: only if a real gap can take a smaller chunk
        shorten = self._shorten_option(victim, events)
        if shorten:
            opts.append(shorten)

        # defer: next day, same time
        defer = self._defer_option(victim, events)
        if defer and self._capacity_ok(defer, victim, events):
            opts.append(defer)

        # stack: only when the persona allows it (P7 by default)
        if self.persona.stack_allowed:
            stack = self._stack_option(victim, events)
            if stack:
                opts.append(stack)

        # skip: H5, or recovery mode
        if victim.hardness == 5 or self.state.get("recovery"):
            opts.append(self._skip(victim))

        return opts

    def _keep(self, victim: Event) -> Plan:
        return Plan(kind="keep", conflict=1, goal=0.0,
                    explanation=[f"「{victim.title}」保留原时间，冲突显式保留。"])

    def _capacity_ok(self, plan: Plan, victim: Event, events: list[Event]) -> bool:
        if not plan.moves:
            return True
        move = plan.moves[0]
        metrics = day_metrics(events, move.to_day, self.persona)
        work = metrics["work_minutes"]
        if move.to_day == victim.day:
            work -= victim.dur
        work += move.to_dur
        window = max(1, metrics["window"])
        return (work / window) <= self.persona.daily_ratio + 0.08

    def _move_options(self, victim: Event, events: list[Event]) -> list[Plan]:
        out: list[tuple[float, Plan]] = []
        local_end = min(6, victim.day + self.persona.radius_days)
        for day in range(victim.day, local_end + 1):
            for start in self._gaps(events, day, victim.dur, victim.id):
                if day == victim.day and start == victim.start:
                    continue
                score, social, energy = self._move_score(victim, day, start)
                reason = self._reason(victim, day, start)
                plan = Plan(
                    kind="move",
                    moves=[Move(victim.id, day, start, victim.dur, reason)],
                    goal=1.0,
                    social=social,
                    energy_mismatch=energy,
                    changes=1,
                    cross_day=(day != victim.day),
                    continuity=1.0,
                    explanation=[reason],
                )
                out.append((score, plan))
        out.sort(key=lambda x: x[0])
        # a far slot is always considered, with an explicit penalty; the
        # persona decides whether preserving the block is worth the jump.
        for day in range(local_end + 1, 7):
            for start in self._gaps(events, day, victim.dur, victim.id):
                score, social, energy = self._move_score(victim, day, start)
                score += 3.0
                reason = f"「{victim.title}」挪到 {day + 1} 天后的 {_fmt(start)}，本地没有空位"
                plan = Plan(
                    kind="move",
                    moves=[Move(victim.id, day, start, victim.dur, reason)],
                    goal=1.0,
                    social=social,
                    energy_mismatch=energy,
                    changes=1,
                    cross_day=True,
                    explanation=[reason],
                )
                out.append((score, plan))
        out.sort(key=lambda x: x[0])
        return [p for _, p in out[:3]]

    def _shorten_option(self, victim: Event, events: list[Event]) -> Plan | None:
        if not victim.splittable or victim.dur <= victim.min_chunk + 10:
            return None
        best: tuple[float, Plan] | None = None
        for day in range(victim.day, min(6, victim.day + 2) + 1):
            for gap in self._gaps(events, day, victim.min_chunk, victim.id):
                free = self._gap_length(events, day, gap, victim.id)
                if free < victim.min_chunk:
                    continue
                new_dur = min(victim.dur - 15, free)
                if new_dur < victim.min_chunk:
                    continue
                score, social, energy = self._move_score(victim, day, gap)
                score += 0.5  # shortening has its own cost
                plan = Plan(
                    kind="shorten",
                    moves=[Move(victim.id, day, gap, new_dur,
                                f"「{victim.title}」缩短到 {new_dur} 分钟，先保住这次")],
                    goal=new_dur / max(1, victim.dur),
                    social=social,
                    energy_mismatch=energy,
                    debt=(victim.dur - new_dur) / 60.0,
                    changes=1,
                    cross_day=(day != victim.day),
                    continuity=0.5,
                    explanation=[f"没有完整空位，先把「{victim.title}」缩到 {new_dur} 分钟。"],
                )
                if best is None or score < best[0]:
                    best = (score, plan)
        return best[1] if best else None

    def _defer_option(self, victim: Event, events: list[Event]) -> Plan | None:
        day = victim.day + 1
        if day > 6:
            return None
        if victim.deadline is not None and day > victim.deadline:
            return None
        if not self._is_free(events, day, victim.start, victim.dur, victim.id):
            return None
        social = self._social_risk(victim)
        plan = Plan(
            kind="defer",
            moves=[Move(victim.id, day, victim.start, victim.dur,
                        f"「{victim.title}」顺延到明天同一时间")],
            goal=1.0,
            social=social,
            changes=1,
            cross_day=True,
            continuity=1.0,
            explanation=[f"今天没有合适空位，把「{victim.title}」整体顺延到明天 {_fmt(victim.start)}。"],
        )
        return plan

    def _stack_option(self, victim: Event, events: list[Event]) -> Plan | None:
        if victim.kind not in ("focus", "task"):
            return None
        for host in events:
            if host.id == victim.id or host.day != victim.day:
                continue
            if host.kind not in ("class", "meeting"):
                continue
            if host.energy == "deep" or host.dur < victim.dur * 0.8:
                continue
            plan = Plan(
                kind="stack",
                stacked=[(victim.id, host.id)],
                goal=0.4,
                changes=1,
                continuity=0.5,
                explanation=[f"把「{victim.title}」挂到「{host.title}」上，按 40% 折算，需你确认。"],
            )
            return plan
        return None

    def _skip(self, victim: Event) -> Plan:
        return Plan(kind="skip", dropped=[victim.id], goal=0.0, changes=1,
                    explanation=[f"「{victim.title}」本次跳过，进入待补。"]) 

    # -- scoring -------------------------------------------------------
    def _materialize(self, events: list[Event], moves: list[Move],
                     dropped: list[str], added: Event | None) -> list[Event]:
        result = [e.clone() for e in events]
        by_id = {e.id: e for e in result}
        for m in moves:
            ev = by_id.get(m.event_id)
            if not ev:
                continue
            ev.day = m.to_day
            ev.start = m.to_start
            ev.dur = m.to_dur
        if dropped:
            gone = set(dropped)
            result = [e for e in result if e.id not in gone]
        if added is not None and not any(e.id == added.id for e in result):
            result.append(added.clone())
        return result

    def _combine(self, combo: Iterable[Plan], added: Event | None,
                 events: list[Event]) -> Plan:
        combo = list(combo)
        moves: list[Move] = []
        dropped: list[str] = []
        stacked: list[tuple[str, str]] = []
        goal = 0.0
        social = 0.0
        conflict = 0
        changes = 1 if added else 0
        energy = 0.0
        debt = 0.0
        continuity = 0.0
        cross_day = False
        explanations: list[str] = []
        kinds: list[str] = []
        for p in combo:
            moves.extend(p.moves)
            dropped.extend(p.dropped)
            stacked.extend(p.stacked)
            goal += p.goal
            social += p.social
            conflict += p.conflict
            changes += p.changes
            energy += p.energy_mismatch
            debt += p.debt
            continuity += p.continuity
            cross_day = cross_day or p.cross_day
            explanations.extend(p.explanation)
            kinds.append(p.kind)
        goal = goal / max(1, len(combo))
        kind = "mixed" if len(set(kinds)) > 1 else kinds[0]
        plan = Plan(kind=kind, moves=moves, dropped=dropped, stacked=stacked,
                    added=added, goal=goal, social=min(1.0, social),
                    conflict=conflict, changes=changes, energy_mismatch=energy,
                    debt=debt, continuity=continuity / max(1, len(combo)),
                    cross_day=cross_day, explanation=explanations)
        self._apply_metrics(plan, events)
        return plan

    def _apply_metrics(self, plan: Plan, events: list[Event]) -> None:
        result = self._materialize(events, plan.moves, plan.dropped, plan.added)
        affected = {m.to_day for m in plan.moves}
        affected.update(e.day for e in events if e.id in set(plan.dropped))
        if plan.added is not None:
            affected.add(plan.added.day)
        if not affected:
            affected = {e.day for e in result}
        metrics = [day_metrics(result, d, self.persona) for d in sorted(affected)]
        if metrics:
            plan.slack_minutes = int(min(m["slack_minutes"] for m in metrics))
            plan.load_ratio = float(max(m["load_ratio"] for m in metrics))
            plan.breathing_minutes = int(min(m["breathing_minutes"] for m in metrics))
        before = {e.id: e.dur for e in events if e.kind == "break"}
        after = {e.id: e.dur for e in result if e.kind == "break"}
        for bid, dur in before.items():
            plan.breathing_loss += max(0, dur - after.get(bid, 0))
        plan.capacity_violation = max(0.0, plan.load_ratio - self.persona.daily_ratio)
        self._score(plan)

    def _score(self, plan: Plan) -> None:
        p = self.persona
        plan.tier = self._tier(plan)
        prompt_pen = {"silent": 0.1, "toast": 0.35, "preview": 0.8, "digest": 0.45}[plan.tier]
        changes_norm = min(1.0, plan.changes / max(1, p.max_changes))
        debt_norm = min(1.0, plan.debt / 2.0)
        slack_pen = max(0.0, (p.slack_reserve - plan.slack_minutes) / max(1, p.slack_reserve))
        capacity_pen = min(1.0, plan.capacity_violation / max(0.05, 1.0 - p.daily_ratio))
        breath_pen = min(1.0, plan.breathing_loss / max(1, p.break_minutes * 2))
        breath_reward = min(1.0, plan.breathing_minutes / max(1, p.break_minutes * 2))
        plan.utility = (
            p.goal_w * plan.goal
            - p.change_w * changes_norm
            - p.social_w * plan.social
            - p.energy_w * min(1.0, plan.energy_mismatch)
            - p.debt_w * debt_norm
            - p.continuity_w * min(1.0, plan.continuity)
            - p.prompt_w * prompt_pen
            - p.slack_w * slack_pen
            - p.capacity_w * capacity_pen
            - p.breathing_w * breath_pen
            + p.breathing_w * 0.8 * breath_reward
        )
        z = (1.0 + p.accept_bias
             - 0.60 * plan.changes
             - 1.00 * plan.social
             - 0.50 * min(1.0, plan.energy_mismatch)
             - 0.80 * plan.conflict
             + 1.20 * plan.goal
             - 0.35 * capacity_pen
             - 0.25 * slack_pen
             - 0.20 * breath_pen
             + 0.20 * breath_reward)
        plan.p_accept = max(0.05, min(0.95, _sigmoid(z)))
        plan.expected = plan.p_accept * plan.utility - (1 - plan.p_accept) * 0.8 * plan.conflict

    def _tier(self, plan: Plan) -> str:
        if self.state.get("stable"):
            return "digest"
        if self.state.get("prompts_today", 0) >= 2:
            return "digest"
        if plan.social > 0:
            return "preview"
        if plan.cross_day:
            return "preview"
        if plan.changes >= 2 or plan.debt > 0:
            return "toast"
        return "silent"

    def _event_day(self, plan: Plan, event_id: str) -> int:
        # only used for the cross-day check; the move carries the target day
        for m in plan.moves:
            if m.event_id == event_id:
                return m.to_day
        return -1

    def _select(self, plans: list[Plan], conflicts: list[Event]) -> tuple[Plan, list[dict[str, Any]], bool]:
        plans.sort(key=lambda p: p.expected, reverse=True)
        best = plans[0]
        keep = next((p for p in plans if p.kind == "keep"), None)

        # Stable mode: do not move a promise or make a batch; keep the conflict.
        if self.state.get("stable") and (best.social > 0 or best.changes > 1):
            if keep is not None:
                best = keep
        # Recovery mode: no cross-day moves; prefer shorten / skip / keep.
        if self.state.get("recovery") and best.kind == "move":
            safe = next((p for p in plans if p.kind in ("shorten", "skip", "keep")), None)
            if safe is not None:
                best = safe

        infeasible = best.conflict > 0 and not any(
            p.kind != "keep" and p.conflict == 0 for p in plans
        )
        alts: list[dict[str, Any]] = []
        seen: set[str] = set()
        for p in plans[1:]:
            if p.kind == best.kind or p.kind in seen:
                continue
            seen.add(p.kind)
            alts.append({
                "kind": p.kind,
                "summary": self._plan_summary(p),
                "expected": round(p.expected, 3),
                "p_accept": round(p.p_accept, 3),
                "changes": p.changes,
            })
            if len(alts) >= 3:
                break
        if infeasible and not alts:
            for p in plans:
                if p is best:
                    continue
                alts.append({
                    "kind": p.kind,
                    "summary": self._plan_summary(p),
                    "expected": round(p.expected, 3),
                    "p_accept": round(p.p_accept, 3),
                    "changes": p.changes,
                })
                if len(alts) >= 2:
                    break
        return best, alts, infeasible

    # -- output --------------------------------------------------------
    def _finish(self, plan: Plan, events: list[Event], added: Event | None,
                t0: float, conflicts: int = 0,
                alternatives: list[dict[str, Any]] | None = None,
                infeasible: bool | None = None) -> Proposal:
        changes, deferred = self._to_changes(plan, events, added)
        if infeasible is None:
            infeasible = plan.conflict > 0
        if not plan.explanation and plan.kind == "add":
            plan.explanation = ["没有冲突，直接写入。"]
        if plan.kind == "keep" and conflicts:
            if infeasible:
                plan.explanation = [f"保留 {conflicts} 处冲突：没有不违反约束的方案。"]
            else:
                plan.explanation = [f"这份建议先保留 {conflicts} 处冲突；备选里有可执行方案。"]
        bill = {
            "changes": plan.changes,
            "goal_pct": round(plan.goal * 100),
            "conflict": plan.conflict,
            "social": plan.social,
            "notifications": sum(1 for m in plan.moves
                                 if self._event_notify(events, m.event_id)),
            "energy_mismatch": round(plan.energy_mismatch, 2),
            "debt_minutes": round(plan.debt * 60),
            "slack_minutes": plan.slack_minutes,
            "breathing_minutes": plan.breathing_minutes,
            "load_pct": round(plan.load_ratio * 100),
            "breathing_loss": plan.breathing_loss,
            "tier": plan.tier,
        }
        summary = self._plan_summary(plan)
        warnings: list[str] = []
        if plan.social > 0:
            warnings.append("涉及他人，改动前需要通知。")
        if plan.energy_mismatch > 0:
            warnings.append("新时段不在你的高效时段。")
        if plan.changes >= 3:
            warnings.append("改动较多，建议先看完整账单。")
        if plan.stacked:
            warnings.append("叠加是估算产出，需要你确认后才计入目标。")
        if plan.conflict > 0 and not infeasible:
            warnings.append("这份建议先保留冲突；备选里有可执行的移动方案。")
        if plan.capacity_violation > 0:
            warnings.append("这天会排得偏满，临时加入的空间会变少。")
        if plan.slack_minutes < self.persona.slack_reserve:
            warnings.append(f"留白低于你的目标（{plan.slack_minutes} / {self.persona.slack_reserve} 分钟）。")
        if plan.breathing_loss > 0:
            warnings.append(f"有 {plan.breathing_loss} 分钟休息被挪动或缩短。")
        if deferred:
            warnings.append(f"还有 {deferred} 项改动放进了下一批。")
        manual: list[dict[str, Any]] = []
        by_id = {e.id: e for e in events}
        for victim_id, host_id in plan.stacked:
            victim = by_id.get(victim_id)
            host = by_id.get(host_id)
            if victim and host:
                manual.append({
                    "action": "stack",
                    "event": victim.title,
                    "host": host.title,
                    "credit": 0.4,
                    "note": "需要用户确认后才计入目标",
                })
        return Proposal(
            summary=summary,
            changes=changes,
            plan=plan,
            alternatives=alternatives or [],
            bill=bill,
            tier=plan.tier,
            explanation=plan.explanation,
            warnings=warnings,
            infeasible=bool(infeasible),
            latency_ms=(time.perf_counter() - t0) * 1000,
            deferred=deferred,
            manual=manual,
        )

    def _plan_summary(self, plan: Plan) -> str:
        if plan.kind == "keep":
            return "保留现有安排，冲突先留着"
        if plan.kind == "add":
            return "直接加入，不影响其他安排"
        if plan.kind == "cancel":
            return "取消该事件，空出的时间先不自动填满"
        if plan.kind == "stack":
            return "把任务挂到已有课堂/会议上，折算产出"
        if plan.kind == "skip":
            return "跳过本次，进入待补"
        bits = [f"动 {plan.changes} 件"]
        if plan.goal >= 0.95:
            bits.append("目标不变")
        elif plan.goal > 0:
            bits.append(f"目标保留 {round(plan.goal * 100)}%")
        else:
            bits.append("目标让位")
        if plan.social > 0:
            bits.append("需通知")
        if plan.debt > 0:
            bits.append(f"欠 {round(plan.debt * 60)} 分钟")
        if plan.slack_minutes:
            bits.append(f"留白 {plan.slack_minutes} 分钟")
        if plan.breathing_loss > 0:
            bits.append(f"休息让位 {plan.breathing_loss} 分钟")
        return "，".join(bits)

    def _to_changes(self, plan: Plan, events: list[Event],
                    added: Event | None) -> tuple[list[dict[str, Any]], int]:
        by_id = {e.id: e for e in events}
        out: list[dict[str, Any]] = []
        for m in plan.moves:
            ev = by_id.get(m.event_id)
            if not ev:
                continue
            out.append({
                "type": "move_event",
                "date": (self.base_date + timedelta(days=m.to_day)).isoformat(),
                "title": ev.title,
                "start": _fmt(m.to_start),
                "duration": int(m.to_dur),
            })
        for event_id in plan.dropped:
            ev = by_id.get(event_id)
            if ev:
                out.append({
                    "type": "remove_event",
                    "date": (self.base_date + timedelta(days=ev.day)).isoformat(),
                    "title": ev.title,
                })
        if added is not None:
            out.insert(0, {
                "type": "add_event",
                "date": (self.base_date + timedelta(days=added.day)).isoformat(),
                "title": added.title,
                "start": _fmt(added.start),
                "duration": int(added.dur),
            })
        # stacking is a manual suggestion, not a protocol change
        deferred = max(0, len(out) - MAX_CHANGES)
        return out[:MAX_CHANGES], deferred

    # -- small helpers -------------------------------------------------
    def _conflicts_of(self, events: list[Event], target: Event) -> list[Event]:
        return [e for e in events if e.id != target.id and _overlap(e, target)]

    def _by_id(self, events: list[Event], event_id: str | None) -> Event | None:
        return next((e for e in events if e.id == event_id), None)

    def _event_notify(self, events: list[Event], event_id: str) -> bool:
        ev = self._by_id(events, event_id)
        return bool(ev and ev.notify)

    def _social_risk(self, ev: Event) -> float:
        if ev.hardness <= 1:
            return 1.0
        if ev.hardness == 2:
            return 0.5
        return 0.0

    def _move_score(self, victim: Event, day: int, start: int) -> tuple[float, float, float]:
        social = self._social_risk(victim)
        energy = 0.7 if victim.energy == "deep" and not _peak(start) else 0.0
        cross = 1.5 if day != victim.day else 0.0
        distance = abs(start - victim.start) / 120.0
        continuity = 0.2 if start != victim.start else 0.0
        score = distance + cross + continuity + energy + social
        return score, social, energy

    def _gaps(self, events: list[Event], day: int, dur: int,
              exclude_id: str | None = None) -> list[int]:
        busy: list[tuple[int, int]] = []
        for e in events:
            if e.day != day or e.id == exclude_id:
                continue
            end = e.end
            if e.kind in ("meeting", "class", "social"):
                end += self.persona.buffer_minutes
            busy.append((max(WORK_START, e.start), min(self.persona.avoid_after, end)))
        busy.sort()
        merged: list[tuple[int, int]] = []
        for s, e in busy:
            if not merged or s > merged[-1][1]:
                merged.append([s, e])
            else:
                merged[-1][1] = max(merged[-1][1], e)
        out: list[int] = []
        cursor = WORK_START
        for s, e in merged:
            if s - cursor >= dur:
                out.append(cursor)
            cursor = max(cursor, e)
        if self.persona.avoid_after - cursor >= dur:
            out.append(cursor)
        return out

    def _gap_length(self, events: list[Event], day: int, start: int,
                    exclude_id: str | None = None) -> int:
        end = start
        for e in events:
            if e.day != day or e.id == exclude_id:
                continue
            if e.start <= end < e.end + self.persona.buffer_minutes:
                end = e.end + self.persona.buffer_minutes
        limit = self.persona.avoid_after
        nxt = limit
        for e in events:
            if e.day != day or e.id == exclude_id:
                continue
            if e.start >= end:
                nxt = min(nxt, e.start)
        return max(0, nxt - end)

    def _is_free(self, events: list[Event], day: int, start: int,
                 dur: int, exclude_id: str | None = None) -> bool:
        for e in events:
            if e.day != day or e.id == exclude_id:
                continue
            if max(start, e.start) < min(start + dur, e.end):
                return False
        return True

    def _reason(self, victim: Event, day: int, start: int) -> str:
        if day == victim.day:
            return f"「{victim.title}」从 {_fmt(victim.start)} 挪到 {_fmt(start)}，避开冲突"
        return f"「{victim.title}」挪到 {day + 1} 天后的 {_fmt(start)}，今天没有连续空位"

    def _empty_proposal(self, t0: float, message: str) -> Proposal:
        plan = Plan(kind="keep", goal=1.0, changes=0, explanation=[message])
        bill = {"changes": 0, "goal_pct": 100, "conflict": 0, "social": 0,
                "notifications": 0, "energy_mismatch": 0, "debt_minutes": 0,
                "tier": "silent"}
        return Proposal(summary=message, changes=[], plan=plan, alternatives=[],
                        bill=bill, tier="silent", explanation=[message],
                        warnings=[], infeasible=False, latency_ms=(time.perf_counter() - t0) * 1000)


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

def _infer_hardness(title: str) -> int:
    hard = ("考试", "面试", "手术", "接孩子", "航班", "答辩")
    promise = ("会议", "会", "课", "周会", "客户", "1:1", "例会", "面试")
    soft = ("社团", "聚餐", "约", "活动")
    habit = ("练习", "习惯", "运动", "健身", "阅读")
    if any(k in title for k in hard):
        return 1
    if any(k in title for k in promise):
        return 1
    if any(k in title for k in soft):
        return 2
    if any(k in title for k in habit):
        return 4
    return 3


def event_from_schedule(item: dict[str, Any], idx: int, base_date: date) -> Event | None:
    try:
        day = (date.fromisoformat(str(item["date"])) - base_date).days
    except (KeyError, ValueError):
        return None
    if day < 0 or day > 6:
        return None
    title = str(item.get("title") or f"事件{idx}")
    raw_start = item.get("start", 9)
    if isinstance(raw_start, str) and ":" in raw_start:
        h, m = raw_start.split(":", 1)
        start = int(h) * 60 + int(float(m))
    else:
        start = int(round(float(raw_start) * 60))
    dur = int(item.get("dur") or item.get("duration") or 60)
    hardness = int(item.get("hardness", _infer_hardness(title)))
    deadline = item.get("deadline")
    if isinstance(deadline, str):
        try:
            deadline = (date.fromisoformat(deadline) - base_date).days
        except ValueError:
            deadline = None
    kind = item.get("kind") or (
        "meeting" if "会" in title else
        "class" if "课" in title else
        "task" if "任务" in title or "作业" in title else
        "habit" if hardness == 4 else "focus"
    )
    return Event(
        id=f"s{idx}",
        title=title,
        day=day,
        start=max(0, min(DAY_MINUTES - 15, start)),
        dur=max(5, min(240, dur)),
        hardness=hardness,
        priority=int(item.get("priority", 2)),
        locked=bool(item.get("locked", hardness == 0)),
        kind=kind,
        splittable=bool(item.get("splittable", kind in ("focus", "task", "habit"))),
        min_chunk=int(item.get("min_chunk", 15)),
        deadline=deadline,
        energy=str(item.get("energy") or ("deep" if kind in ("focus", "task") else "medium")),
        stackable=bool(item.get("stackable", False)),
        notify=bool(item.get("notify", hardness <= 2)),
        source=str(item.get("source", "self")),
    )


def from_state(state: dict[str, Any], base_date: date | None = None
               ) -> tuple[list[Event], PersonaPolicy]:
    base = base_date or date(2026, 11, 2)
    events = []
    for i, item in enumerate(state.get("schedule") or []):
        ev = event_from_schedule(item, i, base)
        if ev:
            events.append(ev)
    label = state.get("persona_type") or state.get("persona") or "P2"
    params = state.get("scheduling_params") or {}
    return events, persona_for(str(label), params)


# ---------------------------------------------------------------------------
# Tiny scenario helper (also used by the benchmark)
# ---------------------------------------------------------------------------

def demo_events(base_date: date | None = None) -> list[Event]:
    base = base_date or date(2026, 11, 2)
    items = [
        {"date": base.isoformat(), "title": "英语面试 · 自我介绍", "start": 14.0, "dur": 30,
         "hardness": 1, "kind": "meeting", "notify": True},
        {"date": base.isoformat(), "title": "整理作品集素材", "start": 11.0, "dur": 45,
         "hardness": 3, "kind": "focus"},
        {"date": base.isoformat(), "title": "产品周会", "start": 10.0, "dur": 60,
         "hardness": 1, "kind": "meeting", "notify": True},
        {"date": (base + timedelta(days=1)).isoformat(), "title": "专注块", "start": 9.0,
         "dur": 90, "hardness": 3, "kind": "focus"},
        {"date": (base + timedelta(days=1)).isoformat(), "title": "设计评审", "start": 14.0,
         "dur": 60, "hardness": 1, "kind": "meeting", "notify": True},
        {"date": (base + timedelta(days=2)).isoformat(), "title": "高数作业", "start": 16.0,
         "dur": 60, "hardness": 3, "kind": "task", "deadline": 2},
    ]
    return [e for e in (event_from_schedule(it, i, base) for i, it in enumerate(items)) if e]


if __name__ == "__main__":
    events = demo_events()
    events = ensure_breathing(events, PERSONAS["P5"])
    planner = Planner(PERSONAS["P5"])
    p = planner.propose(events, Disruption("insert", 0, start=14 * 60, dur=60,
                                           title="临时会议"))
    import json
    print(json.dumps(p.as_dict(), ensure_ascii=False, indent=2))
