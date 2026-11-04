"""Raw conversation capture with PII scrubbing.

Decision (2026-11-04): the product keeps the *text* of a conversation, not
just features, because "how this user writes" is the signal the style
learner needs and a feature vector loses it.

That decision carries obligations — see ``docs/data-policy.md``. This
module implements the two mechanical ones:

* scrub before writing (phone, email, ID card, bank card, street address);
* bounded retention and per-account deletion, same as snapshots.

Storage: ``<conversations_dir>/<user_id>/<YYYY-MM-DD>.jsonl.gz``.
"""

from __future__ import annotations

import gzip
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_RETENTION_DAYS = 90
MAX_TEXT = 4000
DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl\.gz$")
USER_ID = re.compile(r"^u_[0-9a-f]{16}$")

# Order matters: the longest, most specific patterns first, otherwise a
# phone number would be eaten by the bank-card rule.
SCRUBBERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("id_card", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("bank_card", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("address", re.compile(r"[\u4e00-\u9fa5]{2,10}(?:路|街|道|巷|弄|号)\d{1,5}号?")),
)

PLACEHOLDER = {
    "id_card": "[身份证]",
    "bank_card": "[银行卡]",
    "phone": "[手机号]",
    "email": "[邮箱]",
    "address": "[地址]",
}


def _moment(ts: float | None) -> float:
    return time.time() if ts is None else float(ts)


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _folder(root: Path, user_id: str) -> Path:
    if not USER_ID.match(user_id or ""):
        raise ValueError("bad user id")
    return Path(root) / user_id


def scrub(text: str) -> tuple[str, dict[str, int]]:
    """Replace identifying spans; returns text plus what was replaced."""
    if not isinstance(text, str):
        return "", {}
    out = text
    counts: dict[str, int] = {}
    for name, pattern in SCRUBBERS:
        out, hits = pattern.subn(PLACEHOLDER[name], out)
        if hits:
            counts[name] = hits
    return out, counts


def append(
    root: Path,
    user_id: str,
    *,
    session: str,
    role: str,
    text: str,
    ts: float | None = None,
) -> dict[str, Any] | None:
    """Store one turn of the conversation, scrubbed and truncated."""
    if role not in ("user", "assistant"):
        return None
    if not isinstance(text, str):
        return None
    body = text.strip()
    if not body:
        return None
    body = body[:MAX_TEXT]
    clean, counts = scrub(body)
    moment = _moment(ts)
    record = {
        "ts": round(moment, 3),
        "session": str(session)[:64],
        "role": role,
        "text": clean,
        "chars": len(clean),
    }
    if counts:
        record["scrubbed"] = counts
    folder = _folder(root, user_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_day(moment)}.jsonl.gz"
    with gzip.open(path, "at", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


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
    return [
        json.dumps(row, ensure_ascii=False) for row in read(root, user_id)
    ]
