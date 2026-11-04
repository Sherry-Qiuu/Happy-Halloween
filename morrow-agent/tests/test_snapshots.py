"""Offline tests for L0 snapshot archiving.

Run with ``python -m unittest discover -s tests`` from the project root.
Writes go to a temporary folder inside the project, because the sandbox
does not grant access to the system temp directory.
"""

from __future__ import annotations

import gzip
import json
import shutil
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import snapshots  # noqa: E402

USER = "u_0123456789abcdef"
DAY = 86400.0


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = ROOT / ".test-snapshots"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_append_then_read(self):
        state = {"tasks": [{"title": "写开头", "done": False}], "goals": []}
        summary = snapshots.append(self.tmp, USER, state)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["bytes"] > 0, True)

        rows = snapshots.read(self.tmp, USER)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], state)
        self.assertEqual(rows[0]["sha"], summary["sha"])

    def test_two_versions_keep_both(self):
        snapshots.append(self.tmp, USER, {"tasks": [{"done": False}]})
        snapshots.append(self.tmp, USER, {"tasks": [{"done": True}]})

        rows = snapshots.read(self.tmp, USER)
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["sha"], rows[1]["sha"])
        self.assertEqual(rows[-1]["state"]["tasks"][0]["done"], True)

    def test_day_file_is_named_by_local_day(self):
        moment = time.time() - DAY
        snapshots.append(self.tmp, USER, {"x": 1}, ts=moment)
        names = [path.name for path in (self.tmp / USER).iterdir()]
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].endswith(".jsonl.gz"))
        self.assertEqual(len(names[0]), len("2026-01-01.jsonl.gz"))

    def test_file_is_gzip(self):
        snapshots.append(self.tmp, USER, {"x": 1})
        path = next((self.tmp / USER).iterdir())
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            record = json.loads(handle.readline())
        self.assertEqual(record["state"], {"x": 1})

    def test_prune_drops_old_days_only(self):
        old = time.time() - 120 * DAY
        snapshots.append(self.tmp, USER, {"x": "old"}, ts=old)
        snapshots.append(self.tmp, USER, {"x": "new"})
        self.assertEqual(len(snapshots.days(self.tmp, USER)), 2)

        removed = snapshots.prune(self.tmp, retention_days=90)
        self.assertEqual(removed, 1)
        self.assertEqual(len(snapshots.days(self.tmp, USER)), 1)

    def test_remove_user_deletes_everything(self):
        snapshots.append(self.tmp, USER, {"x": 1})
        snapshots.remove_user(self.tmp, USER)
        self.assertFalse((self.tmp / USER).exists())
        self.assertEqual(snapshots.read(self.tmp, USER), [])

    def test_bad_user_id_is_rejected(self):
        with self.assertRaises(ValueError):
            snapshots.append(self.tmp, "../etc", {"x": 1})

    def test_unserialisable_state_is_skipped(self):
        self.assertIsNone(snapshots.append(self.tmp, USER, {"x": object()}))

    def test_total_bytes_counts_files(self):
        snapshots.append(self.tmp, USER, {"x": "a" * 200})
        self.assertGreater(snapshots.total_bytes(self.tmp), 0)


if __name__ == "__main__":
    unittest.main()
