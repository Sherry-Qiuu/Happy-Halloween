"""Offline stand-in for :mod:`deepseek`.

``AGENT_MOCK=1`` keeps the whole stack (SSE, proposal card, apply flow)
demoable without an API key or network access. It is also what the
integration test drives.
"""

from __future__ import annotations

import time
from typing import Any, Iterator

from protocol import fallback_summary, sanitize_changes

_KEYWORDS = ("会", "调整", "推迟", "改到", "冲突", "来不及", "临时")

_DEFAULT_REPLY = (
    "我先把现状读了一遍。今天还剩 3 项待办，"
    "其中最花时间的是整理作品集素材。"
)
_MEETING_REPLY = (
    "14:00 到 16:00 被会议占掉之后，英语练习少了两个小时的余量。"
    "先把练习挪到 16:30，再决定作品集素材要不要往后放。"
)


def _last_user(messages: list[dict[str, str]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return message.get("content", "")
    return ""


def _mock_proposal(text: str, context: str) -> dict[str, Any] | None:
    raw: list[dict[str, Any]] = []
    if context in ("home", "review"):
        raw = [
            {
                "type": "move_event",
                "date": "2026-10-31",
                "title": "英语面试 · 自我介绍",
                "start": "16:30",
                "duration": 30,
            },
            {
                "type": "add_event",
                "date": "2026-10-31",
                "title": "临时会议",
                "start": "14:00",
                "duration": 120,
            },
        ]
    elif context == "project":
        raw = [
            {
                "type": "add_next_action",
                "goal_index": 0,
                "text": "把自我介绍写成 5 句稿子",
                "minutes": 30,
            }
        ]
    changes = sanitize_changes(raw, goals_count=8)
    if not changes:
        return None
    return {"summary": fallback_summary(changes), "changes": changes}


def iter_reply(
    settings: Any,
    messages: list[dict[str, str]],
    goals_count: int | None = None,
    context: str = "home",
) -> Iterator[tuple[str, Any]]:
    """Yield mock deltas, and a proposal for change-shaped requests."""
    del settings
    text = _last_user(messages)
    wants_change = any(word in text for word in _KEYWORDS)
    reply = _MEETING_REPLY if wants_change else _DEFAULT_REPLY
    proposal = _mock_proposal(text, context) if wants_change else None

    for char in reply:
        yield "delta", char
        time.sleep(0.012)
    if proposal:
        yield "proposal", proposal
