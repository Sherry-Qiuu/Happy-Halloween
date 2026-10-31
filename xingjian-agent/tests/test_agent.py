"""Offline tests for the Xingjian agent service.

Run with ``python -m unittest discover -s tests`` from the project root.
No network access and no API key are required: the HTTP tests drive the
mock producer through the real streaming path.
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mock_agent  # noqa: E402
import protocol  # noqa: E402
import server  # noqa: E402
from config import Settings, load_settings  # noqa: E402
from deepseek import ProposalSplitter, parse_proposal  # noqa: E402
from prompts import chat_messages, report_messages  # noqa: E402


class ProtocolTests(unittest.TestCase):
    def test_normalize_time(self) -> None:
        self.assertEqual(protocol.normalize_time("9:05"), "09:05")
        self.assertEqual(protocol.normalize_time("16:30:00"), "16:30")
        self.assertIsNone(protocol.normalize_time("24:00"))
        self.assertIsNone(protocol.normalize_time("下午两点"))
        self.assertIsNone(protocol.normalize_time(None))

    def test_sanitize_keeps_valid_change(self) -> None:
        changes = protocol.sanitize_changes(
            [
                {
                    "type": "move_event",
                    "date": "2026-10-31",
                    "title": "  英语面试 · 自我介绍 ",
                    "start": "16:30",
                    "duration": "30",
                }
            ]
        )
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["title"], "英语面试 · 自我介绍")
        self.assertEqual(changes[0]["start"], "16:30")
        self.assertEqual(changes[0]["duration"], 30)

    def test_sanitize_drops_invalid_and_caps(self) -> None:
        raw = [
            {"type": "unknown", "title": "x"},
            {"type": "add_event", "date": "2026-10-31", "title": "x",
             "start": "25:00", "duration": 30},
            {"type": "add_task", "title": "", "minutes": 20},
            {"type": "add_task", "title": "a", "minutes": 10},
            {"type": "add_task", "title": "b", "minutes": 10},
            {"type": "add_task", "title": "c", "minutes": 10},
            {"type": "add_task", "title": "d", "minutes": 10},
            {"type": "add_task", "title": "e", "minutes": 10},
        ]
        changes = protocol.sanitize_changes(raw, goals_count=2)
        self.assertEqual(len(changes), protocol.MAX_CHANGES)
        self.assertTrue(all(item["type"] == "add_task" for item in changes))

    def test_action_index_respects_goal_count(self) -> None:
        raw = [{"type": "add_next_action", "goal_index": 5,
                "text": "写稿", "minutes": 30}]
        self.assertEqual(protocol.sanitize_changes(raw, goals_count=2), [])

    def test_sse_frame_is_valid(self) -> None:
        frame = protocol.sse("delta", {"text": "你好"}).decode("utf-8")
        self.assertTrue(frame.startswith("event: delta\ndata: "))
        self.assertTrue(frame.endswith("\n\n"))
        payload = json.loads(frame.split("data: ", 1)[1].strip())
        self.assertEqual(payload["text"], "你好")


class SplitterTests(unittest.TestCase):
    TEXT = "先说影响，再给调整。"
    BLOCK = (
        '<<<PROPOSAL>>>\n{"summary": "挪一挪", "changes": '
        '[{"type": "add_task", "title": "回邮件", "minutes": 15}]}\n<<<END>>>'
    )

    def test_stream_across_any_boundary(self) -> None:
        whole = self.TEXT + self.BLOCK + "尾巴"
        for cut in range(1, len(whole)):
            splitter = ProposalSplitter()
            text = splitter.feed(whole[:cut]) + splitter.feed(whole[cut:])
            text += splitter.finish()
            self.assertEqual(text.strip(), self.TEXT, f"cut={cut}")
            proposal = parse_proposal(splitter.block, goals_count=1)
            self.assertIsNotNone(proposal, f"cut={cut}")
            self.assertEqual(proposal["changes"][0]["title"], "回邮件")

    def test_text_only_stream(self) -> None:
        splitter = ProposalSplitter()
        out = "".join(splitter.feed(char) for char in "没有改动")
        out += splitter.finish()
        self.assertEqual(out, "没有改动")
        self.assertEqual(splitter.block, "")

    def test_unterminated_block_is_ignored(self) -> None:
        splitter = ProposalSplitter()
        out = splitter.feed("答复<<<PROPOSAL>>>{\"summary\": \"x\"")
        out += splitter.finish()
        self.assertEqual(out, "答复")
        self.assertIsNone(parse_proposal(splitter.block, goals_count=1))


class PromptTests(unittest.TestCase):
    def test_chat_messages_include_state(self) -> None:
        state = {"today": "2026-10-31", "tasks": []}
        messages = chat_messages(
            [{"role": "user", "content": "开会了"}], state
        )
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("<state>", messages[1]["content"])
        self.assertEqual(messages[-1]["content"], "开会了")

    def test_state_block_is_truncated(self) -> None:
        state = {"blob": "x" * 20_000}
        block = report_messages(state, "写点什么")[1]["content"]
        self.assertLessEqual(len(block), 7000)
        self.assertIn("已截断", block)


class MockProducerTests(unittest.TestCase):
    def test_reschedule_text_yields_proposal(self) -> None:
        settings = load_settings()
        events = list(
            mock_agent.iter_reply(
                settings,
                [{"role": "user", "content": "下午两点开会，帮我调整"}],
                goals_count=2,
                context="home",
            )
        )
        kinds = [kind for kind, _ in events]
        self.assertIn("delta", kinds)
        self.assertIn("proposal", kinds)
        proposal = dict(events)["proposal"]
        self.assertEqual(len(proposal["changes"]), 2)


def _mock_settings() -> Settings:
    base = load_settings()
    return replace(base, port=0, mock=True, web_dir=ROOT / "web")


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = ROOT / f".agent-test-{uuid.uuid4().hex[:8]}"
        cls.temp.mkdir()
        settings = replace(_mock_settings(), data_dir=cls.temp)
        cls.httpd = server.create_server(settings)
        cls.port = cls.httpd.server_address[1]
        user = cls.httpd.accounts.create_user(
            "tester", "correct-horse"
        )
        token = cls.httpd.accounts.create_session(user["id"])
        cls.cookie = f"{server.SESSION_COOKIE}={token}"
        cls.thread = threading.Thread(
            target=cls.httpd.serve_forever, daemon=True
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.temp, ignore_errors=True)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path: str) -> tuple[int, bytes, str]:
        try:
            with urllib.request.urlopen(self.url(path), timeout=10) as resp:
                return resp.status, resp.read(), resp.headers.get(
                    "Content-Type", ""
                )
        except urllib.error.HTTPError as error:
            return error.code, error.read(), ""

    def post(self, path: str, payload: dict) -> tuple[int, dict]:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.url(path),
            data=data,
            headers={
                "Content-Type": "application/json",
                "Cookie": self.cookie,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            return error.code, json.loads(body or "{}")

    def test_health(self) -> None:
        status, body, _ = self.get("/api/agent/health")
        self.assertEqual(status, 200)
        info = json.loads(body.decode("utf-8"))
        self.assertTrue(info["configured"])
        self.assertTrue(info["mock"])

    def test_index_is_served(self) -> None:
        status, body, content_type = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn(b"agent.js", body)

    def test_javascript_mime_type(self) -> None:
        status, _, content_type = self.get("/agent.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", content_type)

    def test_path_traversal_is_blocked(self) -> None:
        status, _, _ = self.get("/../server.py")
        self.assertIn(status, (403, 404))

    def test_chat_streams_deltas_and_proposal(self) -> None:
        payload = {
            "context": "home",
            "messages": [{"role": "user", "content": "下午开会，帮我调整"}],
            "state": {"today": "2026-10-31", "tasks": [], "goals": [{}]},
        }
        request = urllib.request.Request(
            self.url("/api/agent/chat"),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Cookie": self.cookie,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as resp:
            self.assertIn("text/event-stream", resp.headers["Content-Type"])
            raw = resp.read().decode("utf-8")

        names = [
            line.split(":", 1)[1].strip()
            for line in raw.splitlines()
            if line.startswith("event:")
        ]
        self.assertIn("delta", names)
        self.assertIn("proposal", names)
        self.assertEqual(names[-1], "done")

        proposal_frame = raw.split("event: proposal", 1)[1]
        data = proposal_frame.split("data: ", 1)[1].split("\n", 1)[0]
        proposal = json.loads(data)
        self.assertEqual(proposal["changes"][0]["type"], "move_event")

    def test_chat_rejects_bad_payload(self) -> None:
        status, body = self.post(
            "/api/agent/chat",
            {"context": "home", "messages": [], "state": {}},
        )
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_chat_rejects_non_user_tail(self) -> None:
        status, _ = self.post(
            "/api/agent/chat",
            {
                "context": "home",
                "messages": [{"role": "assistant", "content": "hi"}],
                "state": {},
            },
        )
        self.assertEqual(status, 400)

    def test_report(self) -> None:
        status, body = self.post(
            "/api/agent/report",
            {"state": {"tasks": [{"done": True}]}, "reflection": "今天有点乱"},
        )
        self.assertEqual(status, 200)
        self.assertIn("记录到的事实", body["report"])

    def test_unknown_endpoint(self) -> None:
        status, _, _ = self.get("/api/nope")
        self.assertEqual(status, 404)

    def test_agent_requires_login(self) -> None:
        request = urllib.request.Request(
            self.url("/api/agent/chat"),
            data=json.dumps(
                {
                    "context": "home",
                    "messages": [{"role": "user", "content": "hi"}],
                    "state": {},
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(error.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
