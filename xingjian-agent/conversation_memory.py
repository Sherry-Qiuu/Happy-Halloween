"""Per-user long-term conversation memory.

The browser keeps a short window of the current chat; the server keeps
what has to survive a reload, a device switch, and a new session: the
recent turns plus a compact digest that is injected into every prompt.

Everything here is plain JSON so it can live beside the account data:

    {
      "version": 1,
      "turns": [{"ts": 1761900000.0, "context": "home",
                 "user": "…", "assistant": "…", "applied": 2}],
      "notes": ["用户更习惯 25 分钟块"],
      "updated_at": 1761900000.0
    }
"""

from __future__ import annotations

import time
from typing import Any

MAX_TURNS = 240
PROMPT_TURNS = 12
MAX_USER_CHARS = 300
MAX_REPLY_CHARS = 300
MAX_NOTES = 24
SESSION_GAP_SECONDS = 45 * 60


def empty_memory() -> dict[str, Any]:
    now = time.time()
    return {
        "version": 1,
        "turns": [],
        "notes": [],
        "created_at": now,
        "updated_at": now,
    }


def ensure_memory(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return empty_memory()
    memory = {
        "version": 1,
        "turns": list(value.get("turns") or []),
        "notes": [n for n in (value.get("notes") or []) if isinstance(n, str)],
        "created_at": value.get("created_at") or time.time(),
        "updated_at": value.get("updated_at") or time.time(),
    }
    pending = value.get("pending")
    if isinstance(pending, dict):
        memory["pending"] = pending
    return memory


def _clip(text: Any, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    clean = " ".join(text.split())
    return clean[:limit]


def append_turn(
    memory: dict[str, Any],
    *,
    user: str,
    assistant: str,
    context: str = "home",
    applied: int = 0,
    ts: float | None = None,
) -> dict[str, Any]:
    """Fold one exchange into *memory* and return the new document."""
    current = ensure_memory(memory)
    stamp = time.time() if ts is None else float(ts)
    turns = list(current["turns"])
    turns.append(
        {
            "ts": stamp,
            "context": _clip(context, 20) or "home",
            "user": _clip(user, MAX_USER_CHARS),
            "assistant": _clip(assistant, MAX_REPLY_CHARS),
            "applied": max(0, int(applied or 0)),
        }
    )
    current["turns"] = turns[-MAX_TURNS:]
    current["updated_at"] = stamp
    return current


def set_pending(memory: dict[str, Any], clarify: Any) -> dict[str, Any]:
    """Remember the questions the assistant asked, so it does not repeat them."""
    current = ensure_memory(memory)
    if not isinstance(clarify, dict):
        current.pop("pending", None)
        return current
    questions = clarify.get("questions")
    if not isinstance(questions, list) or not questions:
        current.pop("pending", None)
        return current
    current["pending"] = {
        "summary": clarify.get("summary") or "",
        "questions": [
            item.get("q") if isinstance(item, dict) else str(item)
            for item in questions
            if (item.get("q") if isinstance(item, dict) else item)
        ][:3],
        "ts": time.time(),
    }
    return current


def clear_pending(memory: dict[str, Any]) -> dict[str, Any]:
    current = ensure_memory(memory)
    current.pop("pending", None)
    return current


def add_note(memory: dict[str, Any], text: Any) -> dict[str, Any]:
    current = ensure_memory(memory)
    note = _clip(text, 120)
    if not note:
        return current
    notes = [n for n in current["notes"] if n != note]
    notes.append(note)
    current["notes"] = notes[-MAX_NOTES:]
    current["updated_at"] = time.time()
    return current


def _clock(ts: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def digest(memory: dict[str, Any], limit: int = PROMPT_TURNS) -> str:
    """Compact text the model reads before every reply.

    Returns an empty string for a brand new user so the prompt stays the
    same as before.
    """
    current = ensure_memory(memory)
    turns = [t for t in current["turns"] if isinstance(t, dict)]
    notes = current.get("notes") or []
    pending = current.get("pending")
    has_pending = isinstance(pending, dict) and bool(pending.get("questions"))
    if not turns and not notes and not has_pending:
        return ""

    lines: list[str] = []
    now = time.time()
    last = turns[-1]["ts"] if turns else now
    gap = now - float(last or now)
    if turns:
        total = len(turns)
        if gap > SESSION_GAP_SECONDS:
            lines.append(
                f"距离上次对话已过去 {int(gap // 3600)} 小时以上，"
                f"累计聊过 {total} 轮。"
            )
        else:
            lines.append(f"你正在和同一个人连续对话，累计 {total} 轮。")

    for item in turns[-limit:]:
        stamp = _clock(float(item.get("ts") or now))
        user = _clip(item.get("user"), 90)
        reply = _clip(item.get("assistant"), 90)
        if user:
            lines.append(f"- {stamp} 用户说：{user}")
        if reply:
            lines.append(f"  你答：{reply}")

    if has_pending:
        lines.append("上一轮你问了这些问题，还没得到完整回答：")
        for question in pending["questions"]:
            lines.append(f"- {question}")
        lines.append(
            "这一轮用户的回答能对上哪几条，就据此给改动块；"
            "不要重复问同一件事，最多补问一个仍然缺的关键点。"
        )

    if notes:
        lines.append("你记下的稳定偏好：")
        for note in notes[-6:]:
            lines.append(f"- {note}")

    lines.append(
        "这些是跨会话的记忆，可以直接引用；它们比 <state> 旧，"
        "和当前状态冲突时以 <state> 为准。"
    )
    return "\n".join(lines)


def sessions(memory: dict[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    """Group stored turns into sessions for the history panel."""
    current = ensure_memory(memory)
    turns = [t for t in current["turns"] if isinstance(t, dict)]
    groups: list[list[dict[str, Any]]] = []
    for item in turns:
        stamp = float(item.get("ts") or 0)
        if groups:
            previous = float(groups[-1][-1].get("ts") or 0)
            if stamp - previous <= SESSION_GAP_SECONDS:
                groups[-1].append(item)
                continue
        groups.append([item])

    out: list[dict[str, Any]] = []
    for group in groups[-limit:]:
        stamp = float(group[0].get("ts") or 0)
        messages: list[dict[str, str]] = []
        for item in group:
            question = _clip(item.get("user"), 400)
            answer = _clip(item.get("assistant"), 400)
            if question:
                messages.append({"role": "user", "text": question})
            if answer:
                messages.append({"role": "assistant", "text": answer})
        out.append(
            {
                "id": f"s{int(stamp)}",
                "started_at": stamp,
                "label": _clock(stamp),
                "context": group[0].get("context") or "home",
                "count": len(group),
                "preview": _clip(group[0].get("user"), 80),
                "messages": messages,
            }
        )
    return list(reversed(out))
