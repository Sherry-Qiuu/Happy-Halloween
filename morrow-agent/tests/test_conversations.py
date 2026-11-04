"""Offline tests for conversation capture and PII scrubbing."""

from __future__ import annotations

import shutil
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import conversations  # noqa: E402

USER = "u_0123456789abcdef"
DAY = 86400.0


class ScrubTest(unittest.TestCase):
    def test_phone(self):
        text, counts = conversations.scrub("我电话 13812345678，明天打给我")
        self.assertNotIn("13812345678", text)
        self.assertIn("[手机号]", text)
        self.assertEqual(counts["phone"], 1)

    def test_email(self):
        text, _ = conversations.scrub("发到 sherry.qiu@example.com 就行")
        self.assertNotIn("sherry.qiu@example.com", text)
        self.assertIn("[邮箱]", text)

    def test_id_card_wins_over_bank_card(self):
        text, counts = conversations.scrub("身份证 11010519491231002X")
        self.assertNotIn("11010519491231002", text)
        self.assertEqual(counts.get("id_card"), 1)

    def test_plain_numbers_untouched(self):
        text, counts = conversations.scrub("今天做了 30 分钟，还剩 2 件事")
        self.assertEqual(text, "今天做了 30 分钟，还剩 2 件事")
        self.assertEqual(counts, {})


class CaptureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = ROOT / ".test-conversations"
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_append_and_read(self):
        conversations.append(
            self.tmp, USER, session="s1", role="user", text="我早上效率高"
        )
        rows = conversations.read(self.tmp, USER)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["role"], "user")
        self.assertEqual(rows[0]["session"], "s1")

    def test_text_is_scrubbed_on_write(self):
        conversations.append(
            self.tmp, USER, session="s1", role="user", text="打 13900001111"
        )
        row = conversations.read(self.tmp, USER)[0]
        self.assertNotIn("13900001111", row["text"])
        self.assertEqual(row["scrubbed"]["phone"], 1)

    def test_long_text_is_truncated(self):
        conversations.append(
            self.tmp, USER, session="s1", role="user", text="字" * 9000
        )
        row = conversations.read(self.tmp, USER)[0]
        self.assertEqual(len(row["text"]), conversations.MAX_TEXT)

    def test_bad_role_and_empty_text_are_skipped(self):
        self.assertIsNone(
            conversations.append(self.tmp, USER, session="s", role="system", text="x")
        )
        self.assertIsNone(
            conversations.append(self.tmp, USER, session="s", role="user", text="   ")
        )
        self.assertEqual(conversations.read(self.tmp, USER), [])

    def test_prune_and_remove(self):
        conversations.append(
            self.tmp, USER, session="s", role="user", text="旧", ts=time.time() - 200 * DAY
        )
        conversations.append(self.tmp, USER, session="s", role="user", text="新")
        self.assertEqual(conversations.prune(self.tmp, retention_days=90), 1)
        conversations.remove_user(self.tmp, USER)
        self.assertFalse((self.tmp / USER).exists())


if __name__ == "__main__":
    unittest.main()
