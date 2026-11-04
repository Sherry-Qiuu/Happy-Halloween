"""Append-only state snapshots — L0 of the collection ladder.

The app already overwrites the user's state on every save, so the server
threw away history the moment it stored it. This module keeps a compressed
copy of each version, per user, per day:

    <snapshot_dir>/<user_id>/2026-11-04.jsonl.gz

One JSON object per line: ``{"ts", "sha", "bytes", "state"}``. Gzip files
append as concatenated members, which every reader concatenates back, so
appending is a single write and stays atomic enough for this use.

Why this first: it needs no frontend change and it makes "what did the
user see on Tuesday" answerable — the one question nothing else can.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

DEFAULT_RETENTION_DAYS = 90
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024  # refuse to archive absurd payloads
DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl\.gz$")
USER_ID = re.compile(r"^u_[0-9a-f]{16}$")


def _moment(ts: float | None) -> float:
    return time.time() if ts is None else float(ts)


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _folder(root: Path, user_id: str) -> Path:
    if not USER_ID.match(user_id or ""):
        raise ValueError("bad user id")
    return Path(root) / user_id


def append(
    root: Path,
    user_id: str,
    state: Any,
    *,
    ts: float | None = None,
) -> dict[str, Any] | None:
    """Write one snapshot; returns a summary, or ``None`` if skipped."""
    moment = _moment(ts)
    try:
        encoded = json.dumps(
            state, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        return None

    record = {
        "ts": round(moment, 3),
        "sha": hashlib.sha256(encoded).hexdigest()[:16],
        "bytes": len(encoded),
        "state": state,
    }
    folder = _folder(root, user_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_day(moment)}.jsonl.gz"
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with gzip.open(path, "at", encoding="utf-8") as handle:
        handle.write(line)
    return {
        "path": str(path),
        "ts": record["ts"],
        "sha": record["sha"],
        "bytes": record["bytes"],
    }


def days(root: Path, user_id: str) -> list[str]:
    """Archived days for one user, oldest first."""
    folder = _folder(root, user_id)
    if not folder.is_dir():
        return []
    return sorted(
        match.group(1)
        for path in folder.iterdir()
        if (match := DAY_FILE.match(path.name))
    )


def read(
    root: Path,
    user_id: str,
    *,
    day: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Read snapshots back (newest last). ``day`` is ``YYYY-MM-DD``."""
    folder = _folder(root, user_id)
    if not folder.is_dir():
        return []
    names = [f"{day}.jsonl.gz"] if day else [p.name for p in sorted(folder.iterdir())]
    rows: list[dict[str, Any]] = []
    for name in names:
        path = folder / name
        if not path.is_file():
            continue
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    if limit is not None:
        rows = rows[-limit:]
    return rows


def prune(
    root: Path, *, retention_days: int = DEFAULT_RETENTION_DAYS, now: float | None = None
) -> int:
    """Delete day files older than *retention_days*; returns how many."""
    base = Path(root)
    if not base.is_dir():
        return 0
    cutoff = datetime.fromtimestamp(
        _moment(now), timezone.utc
    ) - timedelta(days=max(1, retention_days))
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
    """Delete every snapshot for one account (account deletion)."""
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


def total_bytes(root: Path) -> int:
    total = 0
    for path in Path(root).rglob("*.jsonl.gz"):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def iter_user_days(root: Path) -> Iterator[tuple[str, str]]:
    """Yield ``(user_id, day)`` for every archived file."""
    for folder in Path(root).iterdir() if Path(root).is_dir() else []:
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            match = DAY_FILE.match(path.name)
            if match:
                yield folder.name, match.group(1)
