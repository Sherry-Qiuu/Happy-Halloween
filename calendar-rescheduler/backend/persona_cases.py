# -*- coding: utf-8 -*-
"""Targeted, preference-sensitive cases.

The aggregate benchmark is dominated by "only one feasible slot", where every
policy chooses the same move.  These cases are designed so that *values* decide:
social promise vs goal, local shorten vs far move, stack vs keep, stable vs move.
"""

from __future__ import annotations

from datetime import date

from core import Disruption, Event, PERSONAS, Planner, persona_for

BASE = date(2026, 11, 2)


def ev(id, title, day, start, dur, hardness, kind, **kw):
    return Event(id=id, title=title, day=day, start=start, dur=dur,
                 hardness=hardness, kind=kind, notify=hardness <= 2, **kw)


def base():
    return [
        ev("m1", "客户会", 0, 14 * 60, 60, 1, "meeting"),
        ev("f1", "专注块", 0, 15 * 60, 90, 3, "focus"),
        ev("c1", "专业课", 0, 9 * 60, 90, 1, "class"),
        ev("t1", "作业", 0, 11 * 60, 60, 3, "task"),
        ev("m2", "组会", 1, 14 * 60, 60, 1, "meeting"),
        ev("f2", "专注块", 1, 15 * 60, 90, 3, "focus"),
    ]


CASES = {
    "social_vs_focus": (base(), Disruption("insert", 0, 14 * 60, 60, "临时会")),
    "locked": ([ev("x", "期中考试", 0, 14 * 60, 120, 0, "meeting", locked=True),
                ev("f", "专注块", 0, 16 * 60, 60, 3, "focus")],
               Disruption("insert", 0, 14 * 60, 60, "临时会")),
    "stack": ([ev("c", "水课", 0, 14 * 60, 120, 1, "class", energy="low"),
               ev("f", "作业", 0, 14 * 60, 90, 3, "task")],
              Disruption("insert", 0, 14 * 60, 60, "临时会")),
    "illness": ([ev("f1", "专注块", 2, 9 * 60, 90, 3, "focus"),
                 ev("f2", "作业", 2, 14 * 60, 60, 3, "task")],
                Disruption("illness", 2)),
}


def main():
    print(f"{'case':<18}{'persona':<9}{'plan':<10}{'chg':>4}{'goal':>6}"
          f"{'social':>7}{'p':>6}{'tier':>9}")
    for name, (events, dis) in CASES.items():
        for label in ("P1", "P2", "P5", "P7", "P8"):
            p = Planner(PERSONAS[label], base_date=BASE).propose(events, dis)
            print(f"{name:<18}{label:<9}{p.plan.kind:<10}{p.plan.changes:>4}"
                  f"{p.plan.goal:>6.2f}{p.plan.social:>7.2f}"
                  f"{p.plan.p_accept:>6.2f}{p.plan.tier:>9}")
        print()

    # same case, stable mode on P2
    events, dis = CASES["social_vs_focus"]
    p = Planner(PERSONAS["P2"], {"stable": True}, base_date=BASE).propose(events, dis)
    print("stable P2:", p.plan.kind, p.plan.changes, p.plan.social, p.plan.tier, p.summary)


if __name__ == "__main__":
    main()
