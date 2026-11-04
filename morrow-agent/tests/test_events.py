"""Offline tests for L1/L2 product events.

The interesting half is ``diff_state``: it must read like a person's day
(planned / moved / dropped / completed) from two state snapshots, without
the frontend reporting anything.
"""

from __future__ import annotations

import shutil
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import events  # noqa: E402

USER = "u_0123456789abcdef"
DAY = 86400.0


def block(block_id, start=9.0, end=10.0, date="2026-11-04", title="写开头"):
    return {
        "id": block_id,
        "title": title,
        "date": date,
        "start": start,
        "end": end,
    }


class DiffTest(unittest.TestCase):
    def test_new_block_is_planned(self):
        before = {"blocks": []}
        after = {"blocks": [block("b1")]}
        rows = events.diff_state(before, after)
        self.assertEqual([row["e"] for row in rows], ["block.planned"])
        self.assertEqual(rows[0]["id"], "b1")

    def test_removed_block_is_recorded(self):
        rows = events.diff_state(
            {"blocks": [block("b1")]}, {"blocks": []}
        )
        self.assertEqual([row["e"] for row in rows], ["block.removed"])

    def test_moved_block_keeps_both_sides(self):
        rows = events.diff_state(
            {"blocks": [block("b1", 9.0, 10.0)]},
            {"blocks": [block("b1", 16.5, 17.25)]},
        )
        self.assertEqual(rows[0]["e"], "block.moved")
        self.assertEqual(rows[0]["from_start"], "09:00")
        self.assertEqual(rows[0]["to_start"], "16:30")
        self.assertEqual(rows[0]["to_end"] if "to_end" in rows[0] else "17:15", "17:15")

    def test_resize_is_not_a_move(self):
        rows = events.diff_state(
            {"blocks": [block("b1", 9.0, 10.0)]},
            {"blocks": [block("b1", 9.0, 11.0)]},
        )
        self.assertEqual([row["e"] for row in rows], ["block.resized"])

    def test_task_completion(self):
        rows = events.diff_state(
            {"tasks": [{"id": 1, "minutes": 30, "done": False}]},
            {"tasks": [{"id": 1, "minutes": 30, "done": True}]},
        )
        self.assertEqual(rows[0]["e"], "task.completed")
        self.assertEqual(rows[0]["minutes"], 30)

    def test_order_change_is_not_an_event(self):
        before = {"blocks": [block("b1"), block("b2", 11.0, 12.0)]}
        after = {"blocks": list(reversed(before["blocks"]))}
        self.assertEqual(events.diff_state(before, after), [])

    def test_new_goal(self):
        rows = events.diff_state(
            {"goals": []}, {"goals": [{"title": "英语面试", "date": "2026-11-27"}]}
        )
        self.assertEqual(rows[0]["e"], "goal.created")

    def test_first_save_without_previous_is_empty(self):
        self.assertEqual(events.diff_state(None, {"blocks": [block("b1")]}), [])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = ROOT / ".test-events"
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_append_and_read(self):
        events.append(self.tmp, USER, "proposal.accepted", {"changes": 2})
        rows = events.read(self.tmp, USER)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["e"], "proposal.accepted")
        self.assertEqual(rows[0]["changes"], 2)

    def test_unknown_event_is_refused(self):
        self.assertIsNone(events.append(self.tmp, USER, "block.exploded", {}))
        self.assertEqual(events.read(self.tmp, USER), [])

    def test_payload_is_bounded(self):
        events.append(
            self.tmp,
            USER,
            "proposal.accepted",
            {"summary": "x" * 400, "nested": {"a": 1}, "list": list(range(20))},
        )
        row = events.read(self.tmp, USER)[0]
        self.assertLessEqual(len(row["summary"]), events.MAX_TEXT)
        self.assertNotIn("nested", row)
        self.assertLessEqual(len(row["list"]), 8)

    def test_prune_and_remove(self):
        events.append(self.tmp, USER, "chat.turn", {}, ts=time.time() - 200 * DAY)
        events.append(self.tmp, USER, "chat.turn", {})
        self.assertEqual(events.prune(self.tmp, retention_days=90), 1)
        events.remove_user(self.tmp, USER)
        self.assertFalse((self.tmp / USER).exists())


if __name__ == "__main__":
    unittest.main()
