# -*- coding: utf-8 -*-
"""Contract tests for the rescheduler. Run: python -m unittest test_strategy.py"""
import os
import sys
import time
import unittest
from datetime import date

try:
    from .core import (Disruption, Event, PERSONAS, Planner, day_metrics,
                       demo_events, ensure_breathing, from_state, persona_for)
except ImportError:  # direct execution from rescheduler/
    from core import (Disruption, Event, PERSONAS, Planner, day_metrics,
                      demo_events, ensure_breathing, from_state, persona_for)

# the real wire validator lives in the agent; use it so the test cannot drift
_base = os.path.dirname(os.path.abspath(__file__))
for _rel in ("..", os.path.join("..", "..")):
    _candidate = os.path.abspath(os.path.join(_base, _rel, "xingjian-agent"))
    if os.path.isdir(_candidate):
        sys.path.insert(0, _candidate)
        break
from protocol import sanitize_changes  # noqa: E402

BASE = date(2026, 11, 2)


def ev(id, title, day, start, dur, hardness=3, kind="focus", **kw):
    return Event(id=id, title=title, day=day, start=start, dur=dur,
                 hardness=hardness, kind=kind, notify=hardness <= 2, **kw)


class TestPlanner(unittest.TestCase):
    def test_no_conflict_adds_only(self):
        events = [ev("a", "专注块", 0, 9 * 60, 60)]
        p = Planner(PERSONAS["P2"], base_date=BASE).propose(
            events, Disruption("insert", 0, 10 * 60, 60, "临时会"))
        self.assertEqual(p.plan.kind, "add")
        self.assertEqual(len(p.changes), 1)
        self.assertEqual(p.changes[0]["type"], "add_event")
        self.assertFalse(p.infeasible)

    def test_locked_never_moves(self):
        events = [ev("e", "期中考试", 0, 14 * 60, 120, 0, "meeting", locked=True)]
        p = Planner(PERSONAS["P1"], base_date=BASE).propose(
            events, Disruption("insert", 0, 14 * 60, 60, "临时会"))
        self.assertTrue(p.infeasible)
        self.assertFalse(any(c["type"] == "move_event" and c["title"] == "期中考试"
                             for c in p.changes))

    def test_protocol_cap(self):
        events = [
            ev("a", "A", 0, 9 * 60, 60, 1, "meeting"),
            ev("b", "B", 0, 10 * 60, 60, 1, "meeting"),
            ev("c", "C", 0, 11 * 60, 60, 1, "meeting"),
        ]
        p = Planner(PERSONAS["P1"], base_date=BASE).propose(
            events, Disruption("insert", 0, 9 * 60, 180, "长会"))
        self.assertLessEqual(len(p.changes), 4)
        for c in p.changes:
            self.assertIn(c["type"], ("add_event", "move_event", "remove_event"))
            self.assertIn("date", c)
            self.assertIn("title", c)
        # the actual browser validator must accept every change
        clean = sanitize_changes(p.changes)
        self.assertEqual(len(clean), len(p.changes))

    def test_stable_mode_keeps_social_conflict(self):
        events = [ev("m", "客户会", 0, 14 * 60, 60, 1, "meeting")]
        p = Planner(PERSONAS["P2"], {"stable": True}, base_date=BASE).propose(
            events, Disruption("insert", 0, 14 * 60, 60, "临时会"))
        self.assertEqual(p.plan.kind, "keep")
        self.assertEqual(p.plan.conflict, 1)
        self.assertEqual(p.tier, "digest")

    def test_recovery_does_not_cross_day(self):
        events = [ev("f", "专注块", 0, 14 * 60, 90), ev("g", "作业", 0, 16 * 60, 60)]
        p = Planner(PERSONAS["P1"], {"recovery": True}, base_date=BASE).propose(
            events, Disruption("illness", 0))
        self.assertNotEqual(p.plan.kind, "move")
        self.assertTrue(all(m.to_day == 0 for m in p.plan.moves))

    def test_latency(self):
        events = [ev(f"e{i}", f"E{i}", i % 5, 9 * 60 + i * 30, 60)
                  for i in range(25)]
        planner = Planner(PERSONAS["P5"], base_date=BASE)
        d = Disruption("insert", 0, 10 * 60, 120, "临时会")
        t0 = time.perf_counter()
        for _ in range(200):
            planner.propose(events, d)
        self.assertLess((time.perf_counter() - t0) / 200 * 1000, 20)

    def test_adapter(self):
        state = {"persona_type": "P2", "schedule": [
            {"title": "英语面试 · 自我介绍", "date": "2026-11-02", "start": 14.0, "dur": 30},
            {"title": "产品周会", "date": "2026-11-02", "start": 10.0, "dur": 60},
        ]}
        events, persona = from_state(state, BASE)
        self.assertEqual(len(events), 2)
        self.assertEqual(persona.label, "P2")
        p = Planner(persona, base_date=BASE).propose(
            events, Disruption("insert", 0, 14 * 60, 60, "临时会"))
        self.assertLessEqual(len(p.changes), 4)
        self.assertTrue(p.summary)

    def test_breathing_adds_rest_without_overlap(self):
        base = demo_events()
        events = ensure_breathing(base, PERSONAS["P5"])
        breaks = [e for e in events if e.kind == "break"]
        self.assertGreaterEqual(len(breaks), 2)
        for b in breaks:
            self.assertGreaterEqual(b.dur, 10)
            for e in events:
                if e.id == b.id:
                    continue
                self.assertFalse(
                    e.day == b.day and max(e.start, b.start) < min(e.end, b.end),
                    f"break {b.id} overlaps {e.id}",
                )

    def test_breathing_metrics_and_bill(self):
        events = ensure_breathing(demo_events(), PERSONAS["P2"])
        m = day_metrics(events, 0, PERSONAS["P2"])
        self.assertGreater(m["breathing_minutes"], 0)
        self.assertGreater(m["slack_minutes"], 0)
        self.assertLessEqual(m["load_ratio"], 1.0)
        p = Planner(PERSONAS["P2"], base_date=BASE).propose(
            events, Disruption("insert", 0, 14 * 60, 60, "临时会"))
        self.assertIn("slack_minutes", p.bill)
        self.assertIn("breathing_minutes", p.bill)
        self.assertIn("load_pct", p.bill)


if __name__ == "__main__":
    unittest.main()
