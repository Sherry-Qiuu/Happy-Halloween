"""Offline tests for the output policy.

Each test names the rule it protects: 5.6 (positive framing) or 5.7
(think one way, speak another). Run with
``python -m unittest discover -s tests`` from the project root.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import policy  # noqa: E402


class FramingTest(unittest.TestCase):
    """5.6: mechanical filters that need no judgement."""

    def test_emoji_is_removed(self):
        text, violations = policy.apply("今天进度不错🎉🎉")
        self.assertNotIn("🎉", text)
        self.assertIn("emoji", [item["rule"] for item in violations])

    def test_exclamation_becomes_a_period(self):
        text, violations = policy.apply("这块可以今天做！")
        self.assertNotIn("！", text)
        self.assertIn("exclamation", [item["rule"] for item in violations])

    def test_sentence_cap(self):
        raw = "第一句。第二句。第三句。第四句。第五句。"
        text, violations = policy.apply(raw)
        self.assertEqual(len(text.rstrip("。").split("。")), policy.MAX_SENTENCES)
        self.assertIn("too_long", [item["rule"] for item in violations])

    def test_questions_survive(self):
        text, _ = policy.apply("先把开头做出来，行吗？")
        self.assertTrue(text.endswith("？"))


class BanTest(unittest.TestCase):
    """5.6: evaluate the work, never the person or the mood."""

    def test_chicken_soup_is_dropped(self):
        text, violations = policy.apply("这块还没开始。加油，相信自己！")
        self.assertNotIn("加油", text)
        self.assertIn("chicken_soup", [item["rule"] for item in violations])

    def test_comparison_is_dropped(self):
        text, _ = policy.apply("这周确实被切得很碎。还有人比你更惨呢。")
        self.assertNotIn("比你更惨", text)
        self.assertIn("确实被切得很碎", text)

    def test_false_hope_is_dropped(self):
        text, _ = policy.apply("先排 15 分钟开头。你一定可以的。")
        self.assertNotIn("一定可以", text)
        self.assertIn("15 分钟", text)

    def test_fake_doubt_is_dropped(self):
        text, _ = policy.apply("这次按 50 分钟排。我觉得你不行。")
        self.assertNotIn("你不行", text)

    def test_sentence_with_a_fact_survives_even_if_it_smells(self):
        # A fact outranks a tone rule: removing it would hide information.
        text, violations = policy.apply("你又超时了，实际 52 分钟，预估 30 分钟。")
        self.assertIn("52", text)
        self.assertEqual(violations[0]["kept"], "carries_fact")

    def test_report_mode_keeps_the_paragraphs(self):
        raw = "记录到的事实\n今天记录 65 分钟。\n\n可能的模式\n这一周被切得很碎。\n\n可以带走的判断\n下一步留哪一件？"
        text, _ = policy.apply(raw, max_sentences=None)
        self.assertIn("记录到的事实", text)
        self.assertIn("可以带走的判断", text)


class CrisisTest(unittest.TestCase):
    """5.6 rule 4: the positive framing stops at a safety signal."""

    def test_crisis_detected(self):
        self.assertTrue(policy.detect_crisis("我真的不想活了"))
        self.assertFalse(policy.detect_crisis("今天不想做这个任务"))

    def test_crisis_reply_replaces_everything(self):
        text, violations = policy.apply("随便什么正文", crisis=True)
        self.assertEqual(text, policy.CRISIS_REPLY)
        self.assertEqual(violations[0]["rule"], "crisis_override")
        self.assertIn("400-161-9995", text)


class DraftTest(unittest.TestCase):
    """5.7: the model may think harsher than it speaks, but not lie."""

    def test_draft_keeps_only_checkable_parts(self):
        draft = policy.validate_draft(
            {
                "predicted": 0.62,
                "facts": ["实际 52 分钟", "这周只记录了 2 天", "没写数字的一句"],
                "risk": "他可能又会高估",
                "mood": "ignore me",
            }
        )
        self.assertEqual(draft["predicted"], 0.62)
        self.assertEqual(len(draft["facts"]), 2)
        self.assertNotIn("mood", draft)

    def test_prediction_out_of_range_is_dropped(self):
        self.assertIsNone(policy.validate_draft({"predicted": 1.7}))

    def test_missing_fact_is_detected(self):
        draft = policy.validate_draft({"facts": ["实际 52 分钟"]})
        self.assertEqual(policy.missing_facts(draft, "下次按 50 排。"), ["实际 52 分钟"])
        self.assertEqual(policy.missing_facts(draft, "实际 52 分钟，下次按 50 排。"), [])

    def test_missing_fact_is_appended_not_rewritten(self):
        text, missing = policy.append_missing("下次按 50 排。", ["实际 52 分钟"])
        self.assertTrue(text.startswith("下次按 50 排。"))
        self.assertIn("记录：实际 52 分钟。", text)
        self.assertEqual(missing, ["实际 52 分钟"])

    def test_nothing_to_append_is_a_no_op(self):
        text, missing = policy.append_missing("下次按 50 排。", [])
        self.assertEqual(text, "下次按 50 排。")
        self.assertEqual(missing, [])


class ChunkTest(unittest.TestCase):
    def test_chunks_rebuild_the_text(self):
        text = "先把开头做出来，再决定要不要继续。"
        self.assertEqual("".join(policy.chunks(text, 3)), text)


if __name__ == "__main__":
    unittest.main()
