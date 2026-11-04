"""Append-only product events (L1 / L2 of the collection ladder).

Two things are recorded here:

* **L1** — what the agent proposed and what the human did with it.
  `proposal.accepted` / `proposal.rejected`, emitted from the feedback
  endpoint the UI already calls.
* **L2** — what happened to the calendar itself. These are *derived* from
  consecutive state snapshots, so the frontend needs no instrumentation:
  a block that disappeared was dropped, a block with a new start moved.

Storage: ``<events_dir>/<user_id>/<YYYY-MM-DD>.jsonl.gz``, one JSON object
per line. Same shape as snapshots so one exporter can walk both.
"""

from __future__ import annotations

import gzip
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

DEFAULT_RETENTION_DAYS = 90
MAX_EVENTS_PER_SAVE = 60
MAX_TEXT = 80

DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl\.gz$")
USER_ID = re.compile(r"^u_[0-9a-f]{16}$")

# Anything not in this set is refused: the log must stay queryable.
ALLOWED = frozenset(
    {
        "proposal.shown",
        "proposal.accepted",
        "proposal.rejected",
        "proposal.edited",
        "block.planned",
        "block.moved",
        "block.removed",
        "block.resized",
        "block.completed",
        "actual.logged",
        "actual.removed",
        "task.created",
        "task.completed",
        "task.reopened",
        "goal.created",
        "goal.moved",
        "chat.turn",
        "reflection.saved",
    }
)


def _moment(ts: float | None) -> float:
    return time.time() if ts is None else float(ts)


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _folder(root: Path, user_id: str) -> Path:
    if not USER_ID.match(user_id or ""):
        raise ValueError("bad user id")
    return Path(root) / user_id


def _clean(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Keep payloads small and flat; the event log is not a database."""
    if not isinstance(payload, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in list(payload.items())[:24]:
        name = str(key)[:24]
        if isinstance(value, str):
            out[name] = value[:MAX_TEXT]
        elif isinstance(value, (int, float, bool)) or value is None:
            out[name] = value
        elif isinstance(value, (list, tuple)):
            out[name] = [
                item if isinstance(item, (int, float, bool)) else str(item)[:40]
                for item in list(value)[:8]
            ]
    return out


def append(
    root: Path,
    user_id: str,
    name: str,
    payload: dict[str, Any] | None = None,
    *,
    ts: float | None = None,
) -> dict[str, Any] | None:
    """Write one event; returns it, or ``None`` when refused."""
    if name not in ALLOWED:
        return None
    moment = _moment(ts)
    record = {"ts": round(moment, 3), "e": name, **(_clean(payload))}
    folder = _folder(root, user_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_day(moment)}.jsonl.gz"
    with gzip.open(path, "at", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def append_many(
    root: Path, user_id: str, items: Iterable[dict[str, Any]]
) -> int:
    written = 0
    for item in items:
        if append(root, user_id, item.get("e", ""), item):
            written += 1
        if written >= MAX_EVENTS_PER_SAVE:
            break
    return written


def read(
    root: Path,
    user_id: str,
    *,
    day: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    folder = _folder(root, user_id)
    if not folder.is_dir():
        return []
    names = (
        [f"{day}.jsonl.gz"]
        if day
        else [path.name for path in sorted(folder.iterdir())]
    )
    rows: list[dict[str, Any]] = []
    for name in names:
        path = folder / name
        if not path.is_file():
            continue
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError:
            continue
    return rows[-limit:] if limit else rows


def _rows(state: Any, key: str) -> dict[str, dict[str, Any]]:
    rows = state.get(key) if isinstance(state, dict) else None
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("id") is not None:
            out[str(row["id"])] = row
    return out


def _clock(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    hour = int(number)
    minute = int(round((number - hour) * 60))
    return f"{hour:02d}:{minute:02d}"


def diff_state(prev: Any, nxt: Any) -> list[dict[str, Any]]:
    """Turn "state before" + "state after" into L2 events.

    Blocks and tasks carry ids, so matching is by id — never by position:
    the array is re-sorted by the UI and position diffs are meaningless.
    """
    events: list[dict[str, Any]] = []
    # Without a previous state there is no diff, only an import: emitting
    # "planned" for a whole calendar would poison the log on first save.
    if not isinstance(prev, dict) or not isinstance(nxt, dict):
        return events
    before = prev

    old_blocks = _rows(before, "blocks")
    new_blocks = _rows(nxt, "blocks")
    for block_id, block in new_blocks.items():
        title = str(block.get("title") or "")[:MAX_TEXT]
        if block_id not in old_blocks:
            events.append(
                {
                    "e": "block.planned",
                    "id": block_id,
                    "date": str(block.get("date") or ""),
                    "start": _clock(block.get("start")),
                    "end": _clock(block.get("end")),
                    "title": title,
                    "task": str(block.get("task") or "")[:24],
                }
            )
            continue
        old = old_blocks[block_id]
        moved = (
            str(old.get("date") or "") != str(block.get("date") or "")
            or float(old.get("start") or 0) != float(block.get("start") or 0)
        )
        resized = float(old.get("end") or 0) != float(block.get("end") or 0)
        if moved:
            events.append(
                {
                    "e": "block.moved",
                    "id": block_id,
                    "from_date": str(old.get("date") or ""),
                    "from_start": _clock(old.get("start")),
                    "to_date": str(block.get("date") or ""),
                    "to_start": _clock(block.get("start")),
                }
            )
        elif resized:
            events.append(
                {
                    "e": "block.resized",
                    "id": block_id,
                    "date": str(block.get("date") or ""),
                    "from_end": _clock(old.get("end")),
                    "to_end": _clock(block.get("end")),
                }
            )
    for block_id, block in old_blocks.items():
        if block_id not in new_blocks:
            events.append(
                {
                    "e": "block.removed",
                    "id": block_id,
                    "date": str(block.get("date") or ""),
                    "start": _clock(block.get("start")),
                }
            )

    old_tasks = _rows(before, "tasks")
    new_tasks = _rows(nxt, "tasks")
    for task_id, task in new_tasks.items():
        if task_id not in old_tasks:
            events.append(
                {
                    "e": "task.created",
                    "id": task_id,
                    "minutes": task.get("minutes"),
                }
            )
            continue
        was_done = bool(old_tasks[task_id].get("done"))
        now_done = bool(task.get("done"))
        if now_done and not was_done:
            events.append(
                {
                    "e": "task.completed",
                    "id": task_id,
                    "minutes": task.get("minutes"),
                }
            )
        elif was_done and not now_done:
            events.append({"e": "task.reopened", "id": task_id})

    old_goals = {str(row.get("title")): row for row in before.get("goals") or [] if isinstance(row, dict)}
    for goal in nxt.get("goals") or []:
        if not isinstance(goal, dict):
            continue
        title = str(goal.get("title") or "")[:MAX_TEXT]
        if title not in old_goals:
            events.append(
                {
                    "e": "goal.created",
                    "title": title,
                    "date": str(goal.get("date") or ""),
                }
            )
        elif str(old_goals[title].get("date") or "") != str(goal.get("date") or ""):
            events.append({"e": "goal.moved", "title": title, "date": str(goal.get("date") or "")})

    return events


def prune(
    root: Path,
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    now: float | None = None,
) -> int:
    base = Path(root)
    if not base.is_dir():
        return 0
    cutoff = datetime.fromtimestamp(_moment(now), timezone.utc) - timedelta(
        days=max(1, retention_days)
    )
    removed = 0
    for folder in base.iterdir():
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            match = DAY_FILE.match(path.name)
            if not match:
                continue
            stamp = datetime.strptime(match.group(1), "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            if stamp < cutoff:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
    return removed


def remove_user(root: Path, user_id: str) -> None:
    folder = _folder(root, user_id)
    if not folder.is_dir():
        return
    for path in folder.iterdir():
        try:
            path.unlink()
        except OSError:
            continue
    try:
        folder.rmdir()
    except OSError:
        pass


def export_lines(root: Path, user_id: str) -> list[str]:
    """Every event for one user, as JSON lines (used by account export)."""
    return [
        json.dumps(row, ensure_ascii=False) for row in read(root, user_id)
    ]
