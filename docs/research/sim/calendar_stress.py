# -*- coding: utf-8 -*-
"""
28-day calendar stress simulation.

Compares five generations of "flexible calendar" policy on synthetic personas:
  P0 baseline       : binary fixed/flexible, whole-batch preview, no hardness model
  P1 hardness       : H0-H5 spectrum, H1/H2 ask-first, H0 never moves
  P2 +cost          : minimum-change cost function + adjustment radius
  P3 +parallel      : overlay/stacking with attention discount
  P4 full           : tiered confirmation + change budget + notification drafts + honest failure

The model is deliberately small and interpretable. It is NOT a predictive
model of real users; it is a stress harness for finding second-order effects.
"""
import math
import random
import statistics
import json
import csv
import os
from dataclasses import dataclass, field, asdict
from collections import defaultdict

DAYS = 28
SLOTS = 60          # 08:00 - 23:00, 15-minute slots
MIN_PER_SLOT = 15
WORK_MIN = 600      # 10 hours of "scheduled" is the overload line
STACK_LOAD = 0.40   # credit for a secondary task stacked on a class/meeting

# ----------------------------------------------------------------------------
# Personas
# ----------------------------------------------------------------------------

@dataclass
class Persona:
    key: str
    name: str
    chronotype: str
    weekly_target: int
    adherence: float
    estimate_bias: float
    hardness_bias: float
    parallel_tendency: float
    base_accept: float
    class_load: int
    meeting_load: int
    social_load: int
    weekend_protect: bool = False


PERSONAS = [
    Persona("freshman", "大一新生", "morning", 240, 0.62, 1.25, 0.18, 0.30, 0.60, 8, 3, 2),
    Persona("kaoyan", "考研党", "morning", 480, 0.86, 1.10, 0.25, 0.15, 0.72, 3, 1, 1),
    Persona("club", "社团负责人", "neutral", 300, 0.68, 1.35, 0.30, 0.40, 0.50, 5, 6, 3),
    Persona("intern", "实习+上课", "neutral", 360, 0.75, 1.25, 0.35, 0.30, 0.62, 6, 4, 1),
    Persona("adhd", "高启动摩擦", "evening", 270, 0.48, 1.40, 0.20, 0.20, 0.42, 5, 2, 1),
    Persona("perfection", "完美主义", "morning", 420, 0.80, 1.55, 0.42, 0.10, 0.55, 5, 4, 2),
    Persona("timezone", "跨时区远程", "evening", 360, 0.80, 1.10, 0.30, 0.20, 0.70, 3, 5, 1),
    Persona("caregiver", "照护者", "neutral", 300, 0.70, 1.20, 0.52, 0.25, 0.52, 4, 3, 1),
    Persona("athlete", "运动员", "morning", 330, 0.80, 1.10, 0.40, 0.20, 0.60, 4, 2, 2),
    Persona("freelance", "自由职业", "neutral", 390, 0.76, 1.30, 0.25, 0.30, 0.66, 2, 6, 2),
    Persona("lowdata", "低记录型", "neutral", 300, 0.58, 1.20, 0.20, 0.20, 0.50, 5, 3, 1),
    Persona("examweek", "考试周", "morning", 450, 0.90, 1.00, 0.45, 0.10, 0.75, 7, 2, 0),
]

@dataclass
class Policy:
    key: str
    name: str
    hardness_aware: bool = False
    cost_aware: bool = False
    parallel: bool = False
    tiered_prompt: bool = False
    change_budget: int = 0       # 0 = unlimited
    notify_drafts: bool = False
    honest_failure: bool = False

POLICIES = [
    Policy("P0", "基线：二元固定/可动", False, False, False, False, 0, False, False),
    Policy("P1", "P1 硬度光谱", True, False, False, False, 0, False, False),
    Policy("P2", "P2 +最小代价", True, True, False, False, 0, False, False),
    Policy("P3", "P3 +并行叠加", True, True, True, False, 0, False, False),
    Policy("P4", "P4 完整策略", True, True, True, True, 4, True, True),
]

# ----------------------------------------------------------------------------
# Events / tasks
# ----------------------------------------------------------------------------

@dataclass
class Event:
    id: int
    kind: str                  # class / meeting / focus / task / habit / social
    day: int
    start: int
    dur: int
    hardness: int = 3
    priority: int = 2
    locked: bool = False
    attendance: str = "none"   # none / optional / required
    engagement: str = "deep"   # low / med / deep
    task_id: int = -1
    secondary: int = -1        # stacked event id
    source: str = "self"
    notify: bool = False
    moved: bool = False
    confirmed: bool = True
    deleted: bool = False
    stacked: bool = False


@dataclass
class Task:
    id: int
    name: str
    deadline: int
    remaining: int
    total: int
    missed: bool = False


# ----------------------------------------------------------------------------
# Simulator
# ----------------------------------------------------------------------------

class Sim:
    def __init__(self, persona: Persona, policy: Policy, seed: int):
        self.p = persona
        self.policy = policy
        self.rng = random.Random(seed)
        self.events = {}
        self.tasks = {}
        self.occ = [[set() for _ in range(SLOTS)] for _ in range(DAYS)]
        self.day_energy = [1.0] * DAYS
        self._next_id = 1
        self._next_task = 0

        # metrics
        self.changes = 0
        self.prompts = 0
        self.accepted = 0
        self.rejected = 0
        self.infeasible = 0
        self.deferred = 0
        self.parallel_credit = 0.0
        self.parallel_planned = 0.0
        self.stacks = 0
        self.notification_debt = 0
        self.goal_actual = 0.0
        self.daily_goal = [0.0] * DAYS
        self.conflict_minutes = 0
        self.lock_violations = 0
        self.overload_days = 0
        self.changes_today = 0
        self.prompts_today = 0
        self.deadline_misses = 0
        self.recovery_days = 0
        self.daily_conflicts = [0] * DAYS
        self.boundary_violations = 0

        self.build_calendar()

    # -- occupancy helpers -------------------------------------------------
    def is_free(self, day, start, dur):
        if day < 0 or day >= DAYS or start < 0 or start + dur > SLOTS:
            return False
        for s in range(start, start + dur):
            if self.occ[day][s]:
                return False
        return True

    def add_at(self, ev, allow_overlap=False):
        if not allow_overlap and not self.is_free(ev.day, ev.start, ev.dur):
            return False
        self.events[ev.id] = ev
        for s in range(ev.start, ev.start + ev.dur):
            self.occ[ev.day][s].add(ev.id)
        return True

    def remove(self, ev):
        for s in range(ev.start, ev.start + ev.dur):
            self.occ[ev.day][s].discard(ev.id)
        ev.deleted = True

    def move(self, ev, day, start):
        for s in range(ev.start, ev.start + ev.dur):
            self.occ[ev.day][s].discard(ev.id)
        ev.day, ev.start = day, start
        for s in range(start, start + ev.dur):
            self.occ[day][s].add(ev.id)
        ev.moved = True

    def overlaps(self, ev):
        found = set()
        for s in range(ev.start, ev.start + ev.dur):
            found.update(self.occ[ev.day][s])
        found.discard(ev.id)
        return [self.events[i] for i in found
                if not self.events[i].deleted and not self.events[i].stacked]

    # -- calendar construction ---------------------------------------------
    def _peak(self, slot):
        h = 8 + slot * 0.25
        if self.p.chronotype == "morning":
            return 8 <= h < 12 or 16 <= h < 18
        if self.p.chronotype == "evening":
            return 16 <= h < 21
        return 10 <= h < 12 or 14 <= h < 18

    def _find_free_in_day(self, day, dur, preferred, window=8, peak_only=False):
        if day < 0 or day >= DAYS:
            return None
        starts = []
        for delta in range(0, window + 1):
            for s in (preferred - delta, preferred + delta):
                if s >= 0 and s + dur <= SLOTS:
                    starts.append(s)
        for s in starts:
            if self.is_free(day, s, dur) and (not peak_only or self._peak(s)):
                return s
        # fallback: any free slot in the day
        for s in range(0, SLOTS - dur + 1):
            if self.is_free(day, s, dur) and (not peak_only or self._peak(s)):
                return s
        return None

    def _new_event(self, **kw):
        ev = Event(id=self._next_id, **kw)
        self._next_id += 1
        return ev

    def build_calendar(self):
        # weekly recurring blocks
        for week in range(4):
            base = week * 7
            self._add_classes(base)
            self._add_meetings(base)
            self._add_routines(base)
            self._add_habits(base)
            self._add_social(base)
            self._add_focus(base)
            self._add_tasks(week)

    def _class_pref(self, i):
        if self.p.chronotype == "morning":
            return [0, 8, 24][i % 3]
        if self.p.chronotype == "evening":
            return [24, 32, 40][i % 3]
        return [8, 24, 32][i % 3]

    def _add_classes(self, base):
        for i in range(self.p.class_load):
            day = base + (i % 5)
            pref = self._class_pref(i)
            start = self._find_free_in_day(day, 6, pref, window=10)
            if start is None:
                continue
            h = 1 if self.rng.random() > self.p.hardness_bias else 2
            if h == 1 and self.rng.random() < 0.18:
                h = 0
            ev = self._new_event(kind="class", day=day, start=start, dur=6,
                                 hardness=h, priority=1,
                                 locked=(h == 0), attendance="required",
                                 engagement="low" if h >= 1 else "med",
                                 source="school")
            self.add_at(ev)

    def _add_meetings(self, base):
        for i in range(self.p.meeting_load):
            day = base + (1 + i) % 5
            pref = [16, 24, 36, 8][i % 4]
            start = self._find_free_in_day(day, 4, pref, window=10)
            if start is None:
                continue
            h = 1 if self.rng.random() > self.p.hardness_bias else 2
            ev = self._new_event(kind="meeting", day=day, start=start, dur=4,
                                 hardness=h, priority=1, locked=False,
                                 attendance="required", engagement="med",
                                 source="work" if self.p.key != "club" else "club")
            self.add_at(ev)

    def _add_routines(self, base):
        # lunch and dinner act as soft-fixed anchors on weekdays
        for off in range(5):
            day = base + off
            for start, dur, name in ((0, 2, "prep"), (16, 4, "lunch"),
                                     (38, 2, "commute"), (44, 4, "dinner")):
                if day >= DAYS or not self.is_free(day, start, dur):
                    continue
                ev = self._new_event(kind="routine", day=day, start=start, dur=dur,
                                     hardness=2, priority=1, attendance="optional",
                                     engagement="low", source="self")
                self.add_at(ev)

    def _add_habits(self, base):
        for i, off in enumerate((1, 3, 5)):
            if base + off >= DAYS:
                continue
            start = self._find_free_in_day(base + off, 2, 18, window=6)
            if start is None:
                continue
            ev = self._new_event(kind="habit", day=base + off, start=start, dur=2,
                                 hardness=4, priority=3, engagement="low",
                                 source="self")
            self.add_at(ev)

    def _add_social(self, base):
        for i in range(self.p.social_load):
            off = [4, 5, 2][i % 3]
            if base + off >= DAYS:
                continue
            start = self._find_free_in_day(base + off, 8, 42, window=6)
            if start is None:
                continue
            ev = self._new_event(kind="social", day=base + off, start=start, dur=8,
                                 hardness=2, priority=2, engagement="med",
                                 source="friend", notify=True)
            self.add_at(ev)

    def _add_focus(self, base):
        need = int(math.ceil(self.p.weekly_target / 90.0))
        days = [base + i for i in range(7)]
        placed = 0
        for i in range(need):
            day = days[i % 7]
            pref = [0, 8, 32, 24, 40, 16, 48][i % 7]
            start = self._find_free_in_day(day, 6, pref, window=12, peak_only=True)
            if start is None:
                start = self._find_free_in_day(day, 6, pref, window=12, peak_only=False)
            if start is None:
                continue
            ev = self._new_event(kind="focus", day=day, start=start, dur=6,
                                 hardness=3, priority=2, engagement="deep",
                                 source="self")
            self.add_at(ev)
            placed += 1

    def _add_tasks(self, week):
        if week % 2 != 0:
            return
        t = Task(id=self._next_task, name=f"T{self._next_task}", deadline=week * 7 + 6,
                 remaining=240, total=240)
        self._next_task += 1
        self.tasks[t.id] = t
        # schedule 4 x 60 min before the deadline
        for i in range(4):
            day = min(DAYS - 1, t.deadline - (3 - i))
            start = self._find_free_in_day(day, 4, 8 if i % 2 == 0 else 32, window=10)
            if start is None:
                continue
            ev = self._new_event(kind="task", day=day, start=start, dur=4,
                                 hardness=3, priority=2, task_id=t.id,
                                 engagement="deep", source="self")
            self.add_at(ev)

    # -- confirmation ------------------------------------------------------
    def _confirm(self, cost, batch=1):
        self.prompts += 1
        self.prompts_today += 1
        fatigue = math.exp(-self.prompts_today / 4.0)
        p = self.p.base_accept * fatigue * max(0.15, 1 - 0.30 * cost - 0.06 * (batch - 1))
        if self.rng.random() < p:
            self.accepted += 1
            return True
        self.rejected += 1
        return False

    def _slot_cost(self, ev, d, s):
        cost = 1.0
        if d != ev.day:
            cost += 2.0
        if ev.hardness <= 2:
            cost += 1.5
        if abs(s - ev.start) > 8:
            cost += 0.5
        if d == ev.day + 1 and s == ev.start:
            cost -= 0.5
        if ev.engagement == "deep" and not self._peak(s):
            cost += 0.4
        return max(0.0, min(1.0, cost / 5.0))

    def _allowed(self, ev, d, s):
        if not self.policy.hardness_aware:
            return True
        hour = 8 + s * 0.25
        if ev.kind in ("focus", "task", "class", "meeting", "routine"):
            if d % 7 >= 5:
                return False
            if hour < 9 or hour >= 20:
                return False
        return True

    def _choose_slot(self, ev, horizon=7):
        best = None
        best_cost = 99
        checked = 0
        for d in range(ev.day, min(DAYS, ev.day + horizon)):
            for s in range(0, SLOTS - ev.dur + 1):
                if not self._allowed(ev, d, s):
                    continue
                if not self.is_free(d, s, ev.dur):
                    continue
                if self.policy.cost_aware:
                    c = self._slot_cost(ev, d, s)
                    if c < best_cost:
                        best_cost, best = c, (d, s)
                    checked += 1
                    if checked >= 40 and best is not None:
                        return best, best_cost
                else:
                    return (d, s), 0.2
        return best, best_cost

    def _try_stack(self, ev):
        if ev.kind not in ("focus", "task"):
            return False
        for o in self.events.values():
            if (o.deleted or o.stacked or o.secondary != -1):
                continue
            if o.day != ev.day or o.kind not in ("class", "meeting"):
                continue
            if o.engagement not in ("low", "med"):
                continue
            if o.dur * MIN_PER_SLOT < ev.dur * MIN_PER_SLOT * 0.8:
                continue
            if o.id == ev.id:
                continue
            o.secondary = ev.id
            for s in range(ev.start, ev.start + ev.dur):
                self.occ[ev.day][s].discard(ev.id)
            ev.stacked = True
            self.stacks += 1
            self.parallel_planned += ev.dur * MIN_PER_SLOT * STACK_LOAD
            return True
        return False

    # -- conflict resolution ----------------------------------------------
    def resolve(self, trigger):
        victims = [v for v in self.overlaps(trigger) if not v.deleted]
        if not victims:
            return
        pol = self.policy

        if not pol.hardness_aware:
            movable = [v for v in victims if not v.locked]
            if not movable:
                return
            cost = 0.6
            if not self._confirm(cost, batch=len(movable)):
                return
            for v in movable:
                slot, _ = self._choose_slot(v, horizon=7)
                if slot is None:
                    self.infeasible += 1
                    continue
                self.move(v, *slot)
                self.changes += 1
                self.changes_today += 1
                if v.hardness <= 2 and v.notify:
                    self.notification_debt += 1
            return

        # hardness-aware policies
        for v in victims:
            if v.locked or v.hardness == 0:
                continue
            ask = v.hardness <= 2
            if pol.change_budget and self.changes_today >= pol.change_budget:
                self.deferred += 1
                continue
            slot, cost = self._choose_slot(v, horizon=4 if not pol.cost_aware else 3)
            if slot is None:
                if pol.parallel and self._try_stack(v):
                    continue
                self.infeasible += 1
                continue
            if ask:
                cost = max(cost, 0.55)
            if pol.parallel and cost > 0.55 and self._try_stack(v):
                continue
            prompt = ask or (not pol.tiered_prompt) or cost > 0.34
            if prompt:
                if not self._confirm(cost):
                    continue
            self.move(v, *slot)
            self.changes += 1
            self.changes_today += 1
            if v.hardness <= 2 and v.notify:
                if pol.notify_drafts:
                    v.notify = True
                else:
                    self.notification_debt += 1

    # -- disruption stream -------------------------------------------------
    def _insert(self, kind, day, start, dur, hardness, **kw):
        if day < 0 or day >= DAYS:
            return None
        start = max(0, min(SLOTS - dur, start))
        ev = self._new_event(kind=kind, day=day, start=start, dur=dur,
                             hardness=hardness, **kw)
        self.add_at(ev, allow_overlap=True)
        self.resolve(ev)
        return ev

    def disruptions(self, day):
        r = self.rng
        # scripted stress events, same for every policy/persona
        if day == 9:
            self.day_energy[day] = 0.55
            for ev in list(self.events.values()):
                if ev.day == day and ev.kind in ("focus", "task") and not ev.deleted:
                    if r.random() < 0.5:
                        self.remove(ev)
                        self.changes += 1
                        if ev.task_id >= 0:
                            self.tasks[ev.task_id].remaining += ev.dur * MIN_PER_SLOT
        if day == 16:
            self._insert("meeting", day, 26, 6, 1, attendance="required",
                         engagement="med", source="external", notify=True)
        if day == 19:
            for t in self.tasks.values():
                if not t.missed and t.remaining > 0:
                    t.deadline = max(day + 1, t.deadline - 2)
                    break
        if day == 23:
            self._insert("social", day, 42, 8, 2, attendance="optional",
                         engagement="med", source="friend", notify=True)

        # random background disruptions
        if r.random() < 0.65:
            h = 1 if r.random() < 0.7 else 2
            self._insert("meeting", day, r.randrange(0, 50), r.choice((4, 6, 8)),
                         h, attendance="required", engagement="med",
                         source="external", notify=True)
        if r.random() < 0.30:
            self._insert("meeting", day, r.randrange(0, 50), r.choice((2, 4)),
                         2, attendance="optional", engagement="med",
                         source="external", notify=True)
        if r.random() < 0.40:
            candidates = [e for e in self.events.values()
                          if e.day == day and e.kind in ("meeting", "class")
                          and not e.deleted and not e.stacked]
            if candidates:
                ev = r.choice(candidates)
                extra = r.choice((1, 2, 3))
                ev.dur = min(SLOTS - ev.start, ev.dur + extra)
                for s in range(ev.start, ev.start + ev.dur):
                    self.occ[day][s].add(ev.id)
                self.resolve(ev)
        if r.random() < 0.20:
            candidates = [e for e in self.events.values()
                          if e.day == day and e.kind in ("meeting", "class")
                          and not e.deleted and not e.stacked]
            if candidates:
                self.remove(r.choice(candidates))
                self.changes += 1
        if r.random() < 0.30:
            tasks = [t for t in self.tasks.values() if not t.missed]
            if tasks:
                r.choice(tasks).remaining += r.choice((30, 45, 60))
        if r.random() < 0.25:
            focus = [e for e in self.events.values()
                     if e.day == day and e.kind in ("focus", "task")
                     and not e.deleted and not e.stacked]
            if focus:
                ev = r.choice(focus)
                slot, _ = self._choose_slot(ev, horizon=3)
                if slot:
                    self.move(ev, *slot)
                    self.changes += 1

    # -- daily execution ---------------------------------------------------
    def execute_day(self, day):
        scheduled = sum(e.dur * MIN_PER_SLOT for e in self.events.values()
                        if e.day == day and not e.deleted and not e.stacked)
        if scheduled > WORK_MIN:
            self.overload_days += 1
        overload = scheduled / 600.0

        for ev in list(self.events.values()):
            if ev.day != day or ev.deleted or ev.stacked:
                continue
            energy = 1.0 if self._peak(ev.start) else (0.55 if ev.engagement == "deep" else 0.82)
            p = self.p.adherence * energy * (1 - 0.22 * max(0, overload - 0.7))
            success = self.rng.random() < max(0.05, min(0.98, p))
            actual = ev.dur * MIN_PER_SLOT * (1.0 if success else self.rng.uniform(0.2, 0.6))
            if ev.kind == "focus":
                self.goal_actual += actual
                self.daily_goal[day] += actual
            elif ev.kind == "task":
                if ev.task_id >= 0:
                    t = self.tasks[ev.task_id]
                    t.remaining = max(0, t.remaining - int(actual))
            if ev.secondary != -1:
                sec = self.events.get(ev.secondary)
                if sec and not sec.deleted:
                    credit = sec.dur * MIN_PER_SLOT * STACK_LOAD * self._calibration()
                    self.parallel_credit += credit
                    if sec.kind == "focus":
                        self.goal_actual += credit
                        self.daily_goal[day] += credit
                    elif sec.kind == "task" and sec.task_id >= 0:
                        t = self.tasks[sec.task_id]
                        t.remaining = max(0, t.remaining - int(credit))

        # daily conflicts: overlapping primary pairs
        ids = [e.id for e in self.events.values()
               if e.day == day and not e.deleted and not e.stacked]
        minutes = 0
        for i in range(len(ids)):
            a = self.events[ids[i]]
            for j in range(i + 1, len(ids)):
                b = self.events[ids[j]]
                lo = max(a.start, b.start)
                hi = min(a.start + a.dur, b.start + b.dur)
                if hi > lo:
                    minutes += (hi - lo) * MIN_PER_SLOT
        self.conflict_minutes += minutes
        self.daily_conflicts[day] = minutes

        # deadline checks
        for t in self.tasks.values():
            if t.deadline == day and t.remaining > 0 and not t.missed:
                t.missed = True
                self.deadline_misses += 1

    def _calibration(self):
        return max(0.55, 1.0 - 0.45 * max(0.0, self.p.estimate_bias - 1.0))

    # -- run ---------------------------------------------------------------
    def run(self):
        for day in range(DAYS):
            self.changes_today = 0
            self.prompts_today = 0
            self.disruptions(day)
            self.execute_day(day)
        # recovery after scripted illness on day 9
        daily_target = self.p.weekly_target / 5.0
        count = 0
        recovered = 0
        for d in range(10, DAYS):
            if self.daily_goal[d] >= 0.8 * daily_target:
                recovered += 1
                if recovered >= 2:
                    break
            else:
                recovered = 0
            count += 1
        self.recovery_days = min(10, count)

        # boundary violations: work placed on weekends or after 21:00
        for e in self.events.values():
            if e.deleted or e.stacked or not e.moved:
                continue
            if e.kind in ("focus", "task", "class", "meeting", "routine"):
                if e.day % 7 >= 5 or e.start >= 52:
                    self.boundary_violations += 1

        # final deadline sweep (handles deadlines moved into the past)
        for t in self.tasks.values():
            if not t.missed and t.remaining > 0 and t.deadline < DAYS:
                t.missed = True
                self.deadline_misses += 1

        goal_target = self.p.weekly_target * 4
        backlog = sum(t.remaining for t in self.tasks.values())
        white = [sum(1 for x in row if not x) for row in self.occ]
        return {
            "persona": self.p.key,
            "policy": self.policy.key,
            "goal_attainment": min(1.0, self.goal_actual / max(1, goal_target)),
            "changes_per_day": self.changes / DAYS,
            "prompts_per_day": self.prompts / DAYS,
            "acceptance": self.accepted / max(1, self.prompts),
            "conflict_minutes": self.conflict_minutes,
            "infeasible": self.infeasible,
            "deferred": self.deferred,
            "deadline_misses": self.deadline_misses,
            "notification_debt": self.notification_debt,
            "boundary_violations": self.boundary_violations,
            "backlog_end": backlog,
            "white_space_avg": statistics.mean(white) * MIN_PER_SLOT,
            "parallel_credit": self.parallel_credit,
            "stacks": self.stacks,
            "overload_days": self.overload_days,
            "recovery_days": self.recovery_days,
            "changes": self.changes,
            "prompts": self.prompts,
        }


# ----------------------------------------------------------------------------
# Experiment
# ----------------------------------------------------------------------------

def summarize(rows):
    by_policy = defaultdict(list)
    for r in rows:
        by_policy[r["policy"]].append(r)
    out = []
    for pol in POLICIES:
        rs = by_policy[pol.key]
        if not rs:
            continue
        agg = {"policy": pol.key, "name": pol.name, "n": len(rs)}
        for k in ("goal_attainment", "changes_per_day", "prompts_per_day", "acceptance",
                  "conflict_minutes", "infeasible", "deferred", "deadline_misses",
                  "notification_debt", "boundary_violations", "backlog_end",
                  "white_space_avg", "parallel_credit", "stacks", "overload_days", "recovery_days"):
            vals = [r[k] for r in rs]
            agg[k] = round(statistics.mean(vals), 3)
            agg[k + "_sd"] = round(statistics.pstdev(vals), 3)
        out.append(agg)
    return out


def main():
    seeds = list(range(32))
    rows = []
    for persona in PERSONAS:
        for policy in POLICIES:
            for seed in seeds:
                sim = Sim(persona, policy, seed * 1000 + PERSONAS.index(persona))
                rows.append(sim.run())

    summary = summarize(rows)
    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "runs.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    with open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    with open(os.path.join(outdir, "summary.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        w.writeheader()
        w.writerows(summary)

    print(f"{'policy':<6}{'goal':>7}{'chg/d':>7}{'prompt/d':>9}{'accept':>8}"
          f"{'conflict':>10}{'infeas':>8}{'miss':>6}{'notify':>8}{'bound':>7}"
          f"{'stack':>7}{'backlog':>9}{'recover':>9}")
    for a in summary:
        print(f"{a['policy']:<6}{a['goal_attainment']:>7.2f}{a['changes_per_day']:>7.2f}"
              f"{a['prompts_per_day']:>9.2f}{a['acceptance']:>8.2f}{a['conflict_minutes']:>10.0f}"
              f"{a['infeasible']:>8.1f}{a['deadline_misses']:>6.1f}"
              f"{a['notification_debt']:>8.1f}{a['boundary_violations']:>7.1f}"
              f"{a['stacks']:>7.1f}{a['backlog_end']:>9.0f}{a['recovery_days']:>9.1f}")
    print("wrote", outdir)


if __name__ == "__main__":
    main()
