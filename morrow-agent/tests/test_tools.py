"""Tests for the deterministic tools and the plan validator."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

os.environ["MORROW_SKIP_ENV_FILE"] = "1"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import harness  # noqa: E402
import tools  # noqa: E402

TODAY = date.today()
TOMORROW = (TODAY + timedelta(days=1)).isoformat()


def _state() -> dict:
    return {
        "today": TODAY.isoformat(),
        "now": "09:00",
        "scheduled": [
            {"title": "作品集案例", "date": TOMORROW, "start": "14:00", "duration": 90, "locked": False},
            {"title": "线上课程", "date": TOMORROW, "start": "19:00", "duration": 60, "locked": True},
        ],
        "used": [],
        "tasks": [{"title": "准备英语面试", "minutes": 60, "done": False}],
        "goals": [],
    }


class ToolTests(unittest.TestCase):
    def test_get_day_reads_blocks(self) -> None:
        info = tools.get_day(_state(), TOMORROW)
        self.assertEqual(info["date"], TOMORROW)
        self.assertEqual(len(info["blocks"]), 2)
        self.assertEqual(info["blocks"][0]["start"], "14:00")
        self.assertTrue(info["blocks"][1]["locked"])
        self.assertGreater(info["free_minutes"], 0)

    def test_free_slots_skips_busy_time(self) -> None:
        info = tools.free_slots(_state(), TOMORROW, 60)
        starts = [slot["start"] for slot in info["slots"]]
        self.assertTrue(starts)
        # 14:00-15:30 被占，19:00-20:00 被占
        self.assertNotIn("14:00", starts)
        for start in starts:
            self.assertNotEqual(start, "19:00")

    def test_search_blocks_finds_title(self) -> None:
        info = tools.search_blocks(_state(), "作品集")
        self.assertEqual(info["count"], 1)
        self.assertEqual(info["hits"][0]["date"], TOMORROW)

    def test_relative_dates(self) -> None:
        self.assertEqual(tools._parse_day("今天"), TODAY)
        self.assertEqual(tools._parse_day("明天").isoformat(), TOMORROW)

    def test_bad_arguments_raise(self) -> None:
        with self.assertRaises(tools.ToolError):
            tools.get_day(_state(), "")
        with self.assertRaises(tools.ToolError):
            tools.free_slots(_state(), TOMORROW, 2)
        with self.assertRaises(tools.ToolError):
            tools.call_tool("nope", {}, _state())


class ValidatorTests(unittest.TestCase):
    def test_locked_block_cannot_move(self) -> None:
        proposal = {
            "summary": "改",
            "changes": [
                {"type": "move_event", "date": TOMORROW, "title": "线上课程",
                 "start": "21:00", "duration": 60}
            ],
        }
        issues = harness.validate_plan(_state(), proposal)
        self.assertTrue(any("锁定" in item for item in issues))

    def test_missing_target_is_reported(self) -> None:
        proposal = {
            "summary": "删",
            "changes": [{"type": "remove_event", "date": TOMORROW, "title": "不存在的事"}],
        }
        issues = harness.validate_plan(_state(), proposal)
        self.assertTrue(any("找不到" in item for item in issues))

    def test_overlap_is_reported(self) -> None:
        proposal = {
            "summary": "加",
            "changes": [
                {"type": "add_event", "date": TOMORROW, "title": "会议",
                 "start": "14:30", "duration": 60}
            ],
        }
        issues = harness.validate_plan(_state(), proposal)
        self.assertTrue(any("冲突" in item for item in issues))

    def test_clean_proposal_passes(self) -> None:
        proposal = {
            "summary": "加",
            "changes": [
                {"type": "add_event", "date": TOMORROW, "title": "复盘",
                 "start": "21:00", "duration": 60}
            ],
        }
        self.assertEqual(harness.validate_plan(_state(), proposal), [])

    def test_tool_schema_names_match_impl(self) -> None:
        declared = {item["function"]["name"] for item in tools.TOOL_SCHEMAS}
        self.assertEqual(declared, set(tools.TOOL_IMPL))


if __name__ == "__main__":
    unittest.main()
