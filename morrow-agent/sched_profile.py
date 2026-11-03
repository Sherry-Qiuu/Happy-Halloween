"""Scheduling profile: posterior indicators, drift, expectation, style.

Implements the rules in ``用户画像类型与排布策略.md`` (v0.2). Standard
library only, like the rest of the service; nothing here talks to the
network or to the model.

The module is split along the three boundaries the document insists on:

* ``observe*`` — statistics. Only these move an indicator, and they
  refuse weak evidence sources (7.12).
* ``validate_delta`` / ``apply_delta`` — the model's write path. The model
  may only write enum slots, and every write must quote the user (P-2).
* ``prompt_block`` / ``scheduling_params`` / ``bet`` — the read path. The
  model sees parameters, never labels; the reply layer sees a bet, never
  the raw prediction (7.8).

A profile is a plain JSON-serialisable dict so it can live in the per-user
record of ``accounts.AccountStore``.
"""

from __future__ import annotations

import copy
import json
import math
import re
import time
import unicodedata
from typing import Any, Iterable

# --------------------------------------------------------------------------
# Tuning constants. Every number here is a first guess, meant to be moved
# by real data (7.5-7.13).
# --------------------------------------------------------------------------

TAU_DAYS = 21.0  # time decay, ~14 day half life (P-7)
DRIFT_TAU_DIVISOR = 3.0  # how much faster decay gets once drift is confirmed
PRIOR_K = 5.0  # shrinkage strength: w = n / (n + PRIOR_K) (7.9)
DRIFT_WINDOW = 3  # consecutive same-side residuals (P-5)
DRIFT_Z = 1.5  # ... each at least this large
OUTLIER_Z = 3.0  # single residual this large is noise, not signal
MAX_DELTA = 3  # model writes per turn (P-4)
MAX_SAMPLES = 40  # per indicator, keeps the record small
MAX_PROMPT_CHARS = 200  # <persona> block budget (7.2)
MAX_QUOTE = 30  # characters the model may quote (P-2)
PURGE_WEIGHT = 0.15  # decayed weight below which a slot may be purged (7.10)
STYLE_ALPHA = 0.2  # EMA for language style

EXPECTATION_LEVELS = ("guard", "neutral", "challenge")
EXPECTATION_QUANTILE = {"guard": 0.25, "neutral": 0.50, "challenge": 0.85}
BET_DELTA = {"guard": -0.15, "neutral": 0.0, "challenge": 0.15}

LEVEL_LABELS = {"guard": "守护", "neutral": "中立", "challenge": "挑战"}

INDICATORS: dict[str, dict[str, Any]] = {
    "estimate_bias": {"kind": "lognormal", "prior": 1.2, "mad": 0.3, "label": "估时系数"},
    "weekly_load": {"kind": "lognormal", "prior": 8.0, "mad": 2.0, "label": "周可投入"},
    "start_delay": {"kind": "lognormal", "prior": 2.0, "mad": 1.0, "label": "启动延迟"},
    "feedback_delay": {"kind": "lognormal", "prior": 1.0, "mad": 0.5, "label": "反馈时延"},
    "block_success": {"kind": "beta", "label": "块完成率"},
    "interrupt": {"kind": "beta", "label": "中断率"},
}

BLOCK_BUCKETS = ("15", "25", "45", "90")

# 7.12: what may update what. "model" is never a source, and a user's
# reaction may move a preference but never a capability estimate.
SOURCE_RULES = {
    "capability": {"calendar"},
    "preference": {"self_report", "reaction"},
}
WEAK_SOURCE_WEIGHT = {"self_report": 0.3}

# P-3: the model must not accept instructions about itself as evidence.
META_INSTRUCTION_MARKERS = (
    "记住",
    "以后都",
    "你要认为",
    "你必须",
    "从现在起",
    "每次都要",
    "你要记住",
    "重新评价我",
)

SLOTS: dict[str, tuple[str, tuple[str, ...] | int]] = {
    "rhythm": ("enum", ("morning", "afternoon", "evening", "late")),
    "block": ("enum", BLOCK_BUCKETS),
    "buffer": ("enum", ("5", "10", "15", "20")),
    "avoid": ("list", 3),
    "density": ("enum", ("sparse", "normal", "dense")),
    "goal_count": ("enum", ("1-2", "3-5", "6+")),
    "note": ("text", 40),
}

SLOT_LABELS = {
    "rhythm": "节奏",
    "block": "单块",
    "buffer": "缓冲",
    "avoid": "禁排",
    "density": "松紧",
    "goal_count": "目标数",
    "note": "备注",
}

VALUE_LABELS: dict[str, dict[str, str]] = {
    "rhythm": {
        "morning": "早晨高效",
        "afternoon": "下午高效",
        "evening": "晚上高效",
        "late": "深夜高效",
    },
    "density": {"sparse": "留白", "normal": "正常", "dense": "紧凑"},
}

_EMOJI_RANGES = (
    (0x1F300, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x1F000, 0x1F0FF),
    (0x2190, 0x21FF),
)
_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")
_CASUAL = ("啊", "吧", "嘛", "呢", "呀", "哦", "啦", "哈", "嗯", "呗")
_FORMAL = ("您", "请问", "烦请", "感谢", "建议", "请确认", "您好", "拙见")
_SELF_REF = ("俺", "本人", "我")


class ProfileError(ValueError):
    """Raised when a profile payload cannot be used at all."""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _now(ts: float | None = None) -> float:
    return float(ts) if ts is not None else time.time()


def _age_days(sample_ts: float, now: float) -> float:
    return max(0.0, (now - sample_ts) / 86400.0)


def _weight(sample_ts: float, now: float, tau: float) -> float:
    return math.exp(-_age_days(sample_ts, now) / tau)


def weighted_median(pairs: Iterable[tuple[float, float]]) -> float | None:
    """Median of *pairs* of ``(value, weight)``."""
    ordered = sorted((v, w) for v, w in pairs if w > 0)
    total = sum(w for _, w in ordered)
    if total <= 0:
        return None
    acc = 0.0
    for value, weight in ordered:
        acc += weight
        if acc >= total / 2.0:
            return value
    return ordered[-1][0]


def weighted_mad(pairs: Iterable[tuple[float, float]], median: float) -> float:
    """Weighted median absolute deviation — the robust spread."""
    return weighted_median([(abs(v - median), w) for v, w in pairs]) or 0.0


def _tau_for(record: dict[str, Any]) -> float:
    divisor = record.get("tau_divisor", 1.0)
    return TAU_DAYS / max(1.0, float(divisor))


def _decayed_samples(
    record: dict[str, Any], now: float
) -> list[tuple[float, float]]:
    tau = _tau_for(record)
    out: list[tuple[float, float]] = []
    for sample in record.get("samples", []):
        try:
            value = float(sample["v"])
            stamp = float(sample["t"])
        except (KeyError, TypeError, ValueError):
            continue
        weight = _weight(stamp, now, tau)
        if weight >= 0.02:
            out.append((value, weight))
    return out


def _spread(record: dict[str, Any], median: float) -> float:
    """Effective MAD, floored so a flat history stays usable."""
    pairs = _decayed_samples(record, _now(record.get("now")))
    mad = weighted_mad(pairs, median) if pairs else 0.0
    floor = max(0.05, abs(median) * 0.12)
    return mad if mad > floor * 0.5 else floor


def _empty_indicator(key: str) -> dict[str, Any]:
    spec = INDICATORS[key]
    if spec["kind"] == "beta":
        return {"kind": "beta", "buckets": {}, "turn": 0}
    return {
        "kind": "lognormal",
        "samples": [],
        "median": spec["prior"],
        "mad": spec["mad"],
        "n": 0,
        "residuals": [],
        "state": "normal",
        "tau_divisor": 1.0,
        "turn": 0,
    }


def empty_profile() -> dict[str, Any]:
    """A profile with nothing known yet (7.3 cold start)."""
    return {
        "version": 0,
        "indicators": {},
        "slots": {},
        "expectation": {
            "level": "neutral",
            "confidence": "low",
            "votes": {"guard": 0, "neutral": 1, "challenge": 0},
            "streak": 0,
            "evidence": 0,
        },
        "style": {},
        "outliers": {},
        "retired": {},
        "turn": 0,
    }


def ensure_profile(profile: Any) -> dict[str, Any]:
    """Return a usable profile dict, repairing a sparse one."""
    if not isinstance(profile, dict):
        return empty_profile()
    base = empty_profile()
    base.update(profile)
    if not isinstance(base.get("indicators"), dict):
        base["indicators"] = {}
    if not isinstance(base.get("slots"), dict):
        base["slots"] = {}
    if not isinstance(base.get("expectation"), dict):
        base["expectation"] = empty_profile()["expectation"]
    for key, value in empty_profile()["expectation"].items():
        base["expectation"].setdefault(key, value)
    if not isinstance(base.get("style"), dict):
        base["style"] = {}
    if not isinstance(base.get("outliers"), dict):
        base["outliers"] = {}
    if not isinstance(base.get("retired"), dict):
        base["retired"] = {}
    return base


# --------------------------------------------------------------------------
# Observation path (statistics only)
# --------------------------------------------------------------------------


def is_allowed_source(kind: str, source: str) -> bool:
    """Whether *source* may move a *kind* indicator (7.12)."""
    allowed = SOURCE_RULES.get(kind)
    if not allowed:
        return False
    return source in allowed


def observe(
    profile: dict[str, Any],
    key: str,
    value: float | bool,
    *,
    bucket: str | None = None,
    ts: float | None = None,
    source: str = "calendar",
    kind: str = "capability",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fold one observation into *profile*.

    Returns ``(profile, outcome)`` where *outcome* carries ``status`` of
    ``"accepted"``, ``"outlier"`` or ``"rejected"`` plus a reason, so the
    caller can log what happened without re-deriving it.
    """
    if key not in INDICATORS:
        raise ProfileError(f"unknown indicator: {key}")
    if not is_allowed_source(kind, source):
        return profile, {
            "status": "rejected",
            "reason": "source_not_allowed",
            "key": key,
            "source": source,
        }

    now = _now(ts)
    result = copy.deepcopy(ensure_profile(profile))
    spec = INDICATORS[key]
    record = result["indicators"].get(key) or _empty_indicator(key)
    record["now"] = now

    if spec["kind"] == "beta":
        return _observe_beta(result, record, key, value, bucket, now, source)

    try:
        number = float(value)
    except (TypeError, ValueError):
        return profile, {"status": "rejected", "reason": "bad_value", "key": key}
    if not math.isfinite(number) or number <= 0:
        return profile, {"status": "rejected", "reason": "bad_value", "key": key}

    median = float(record.get("median") or spec["prior"])
    spread = _spread(record, median)
    residual = (number - median) / spread

    # P-5: one big excursion is noise. Three in a row is a step change.
    drift_confirmed = False
    if record.get("n", 0) >= 3 and abs(residual) > OUTLIER_Z:
        sign = 1 if residual > 0 else -1
        previous = int(record.get("outlier_sign", 0))
        streak = int(record.get("outlier_streak", 0)) + 1 if previous == sign else 1
        record["outlier_streak"] = streak
        record["outlier_sign"] = sign
        if streak < DRIFT_WINDOW:
            outliers = result.setdefault("outliers", {})
            outliers[key] = int(outliers.get(key, 0)) + 1
            record["state"] = "outlier"
            result["indicators"][key] = record
            return result, {
                "status": "outlier",
                "reason": "single_excursion",
                "key": key,
                "residual": round(residual, 3),
            }
        # A repeated excursion stops being noise: read it at the faster
        # decay so the posterior actually follows the new level.
        drift_confirmed = True
        record["tau_divisor"] = DRIFT_TAU_DIVISOR

    weight = WEAK_SOURCE_WEIGHT.get(source, 1.0)
    if weight < 1.0:
        # A self report is a weak prior: keep it, but only partially.
        number = median + (number - median) * weight

    samples = record.setdefault("samples", [])
    samples.append({"v": round(number, 4), "t": now, "s": source})
    del samples[:-MAX_SAMPLES]

    pairs = _decayed_samples(record, now)
    new_median = weighted_median(pairs) or number
    record["median"] = round(new_median, 4)
    record["mad"] = round(weighted_mad(pairs, new_median) or spec["mad"], 4)
    record["n"] = int(record.get("n", 0)) + (1 if weight >= 1.0 else 0)
    record.setdefault("prior", spec["prior"])
    record["outlier_streak"] = 0
    record["outlier_sign"] = 0
    record["state"] = "normal"

    drift = drift_confirmed
    if not drift:
        drift = _track_drift(record, number, median, spread)
    if drift:
        record["tau_divisor"] = DRIFT_TAU_DIVISOR
        record["drift_at"] = now
        record["state"] = "drift"
        # Re-read the samples at the faster decay so the posterior actually
        # moves instead of waiting a full tau (P-5 "加速调整").
        pairs = _decayed_samples(record, now)
        record["median"] = round(weighted_median(pairs) or record["median"], 4)
        record["mad"] = round(
            weighted_mad(pairs, record["median"]) or record["mad"], 4
        )

    result["indicators"][key] = record
    return result, {
        "status": "accepted",
        "reason": "drift" if drift else "update",
        "key": key,
        "median": record["median"],
        "n": record["n"],
    }


def _observe_beta(
    profile: dict[str, Any],
    record: dict[str, Any],
    key: str,
    value: float | bool,
    bucket: str | None,
    now: float,
    source: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    label = str(bucket or "")
    if label not in BLOCK_BUCKETS:
        return profile, {"status": "rejected", "reason": "bad_bucket", "key": key}
    weight = 1.0
    buckets = record.setdefault("buckets", {})
    alpha, beta = buckets.get(label, [0.0, 0.0])
    success = bool(value)
    increment = WEAK_SOURCE_WEIGHT.get(source, 1.0)
    if success:
        alpha += weight * increment
    else:
        beta += weight * increment
    buckets[label] = [round(alpha, 3), round(beta, 3)]
    record["turn"] = int(record.get("turn", 0))
    profile["indicators"][key] = record
    return profile, {
        "status": "accepted",
        "reason": "update",
        "key": key,
        "bucket": label,
    }


def _track_drift(
    record: dict[str, Any], value: float, median: float, spread: float
) -> bool:
    """P-5: three consecutive same-side residuals beyond DRIFT_Z."""
    residual = (value - median) / spread if spread else 0.0
    residuals = list(record.get("residuals") or [])
    residuals.append(round(residual, 3))
    del residuals[:-DRIFT_WINDOW]
    record["residuals"] = residuals
    if len(residuals) < DRIFT_WINDOW:
        record["state"] = "normal"
        return False
    same_side = all(r > DRIFT_Z for r in residuals) or all(
        r < -DRIFT_Z for r in residuals
    )
    record["state"] = "drift" if same_side else "normal"
    return same_side


def block_rate(profile: dict[str, Any], bucket: str) -> float | None:
    """Posterior success rate for a block length, or ``None`` if unseen."""
    record = ensure_profile(profile)["indicators"].get("block_success") or {}
    alpha, beta = (record.get("buckets") or {}).get(bucket, [0.0, 0.0])
    total = alpha + beta
    if total < 1.0:
        return None
    return alpha / total


def shrunk(
    profile: dict[str, Any], key: str, prior: float | None = None
) -> float:
    """``w * personal + (1 - w) * prior`` with ``w = n / (n + k)`` (7.9)."""
    spec = INDICATORS.get(key, {})
    base = float(spec.get("prior", 1.0) if prior is None else prior)
    record = ensure_profile(profile)["indicators"].get(key) or {}
    n = float(record.get("n", 0))
    median = record.get("median")
    if not isinstance(median, (int, float)):
        return base
    w = n / (n + PRIOR_K) if n > 0 else 0.0
    return round(w * float(median) + (1 - w) * base, 4)


# --------------------------------------------------------------------------
# Expectation posture (7.8)
# --------------------------------------------------------------------------

_VOTE_SIGNALS = {
    "accept_conservative": "guard",
    "self_blame": "guard",
    "reject_conservative": "challenge",
    "ask_harder": "challenge",
    "ask_stretch": "challenge",
    "rebound": "challenge",
}


def observe_expectation(
    profile: dict[str, Any], signal: str, *, ts: float | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Move the expectation posture one vote; needs 3 in a row (7.8)."""
    vote = _VOTE_SIGNALS.get(signal)
    if vote is None:
        return profile, {"status": "rejected", "reason": "unknown_signal"}

    result = copy.deepcopy(ensure_profile(profile))
    state = result["expectation"]
    state["votes"] = {
        key: int((state.get("votes") or {}).get(key, 0))
        for key in EXPECTATION_LEVELS
    }
    state["votes"][vote] += 1
    state["evidence"] = int(state.get("evidence", 0)) + 1

    current = state.get("level", "neutral")
    last_vote = state.get("last_vote")
    # A streak counts *this* cue repeating, not votes in general: guard,
    # challenge, guard must not read as three in a row.
    streak = int(state.get("streak", 0)) + 1 if last_vote == vote else 1
    state["last_vote"] = vote
    moved = False
    if streak >= DRIFT_WINDOW and vote != current:
        index = EXPECTATION_LEVELS.index(current)
        step = 1 if EXPECTATION_LEVELS.index(vote) > index else -1
        # One level at a time: the cue must be unambiguous before the
        # posture jumps from guard straight to challenge.
        state["level"] = EXPECTATION_LEVELS[max(0, min(len(EXPECTATION_LEVELS) - 1, index + step))]
        streak = 0
        moved = True
    state["streak"] = streak
    state["confidence"] = _confidence_for(int(state.get("evidence", 0)))
    result["expectation"] = state
    result["version"] = int(result.get("version", 0)) + 1
    return result, {
        "status": "accepted",
        "level": state["level"],
        "moved": moved,
        "streak": streak,
    }


def _confidence_for(evidence: int) -> str:
    if evidence >= 7:
        return "high"
    if evidence >= 3:
        return "mid"
    return "low"


def expectation_level(profile: dict[str, Any]) -> str:
    level = ensure_profile(profile)["expectation"].get("level", "neutral")
    return level if level in EXPECTATION_LEVELS else "neutral"


# --------------------------------------------------------------------------
# Language style (7.11)
# --------------------------------------------------------------------------


def _count_emoji(text: str) -> int:
    count = 0
    for char in text:
        code = ord(char)
        if any(low <= code <= high for low, high in _EMOJI_RANGES):
            count += 1
        elif unicodedata.category(char) == "So":
            count += 1
    return count


def _style_features(text: str) -> dict[str, Any]:
    compact = "".join(text.split())
    length = len(compact)
    casual = sum(compact.count(marker) for marker in _CASUAL)
    formal = sum(compact.count(marker) for marker in _FORMAL)
    cjk = len(_CJK.findall(compact))
    latin = len(_LATIN.findall(compact))
    return {
        "length": "short" if length < 20 else ("medium" if length < 50 else "long"),
        "formality": "casual" if casual > formal else ("formal" if formal > 0 else "neutral"),
        "emoji": "frequent" if _count_emoji(text) >= 2 else ("sparse" if _count_emoji(text) else "none"),
        "mixed": "high" if cjk and latin / max(1, cjk + latin) > 0.25 else "low",
        "self_ref": next((word for word in _SELF_REF if word in compact), "我"),
    }


def observe_style(
    profile: dict[str, Any], text: str, *, source: str = "user"
) -> dict[str, Any]:
    """Update language style from *user* text only (7.11 rule 4)."""
    if source != "user":
        raise ProfileError("style may only learn from user messages")
    if not isinstance(text, str) or not text.strip():
        return ensure_profile(profile)

    result = copy.deepcopy(ensure_profile(profile))
    style = result["style"]
    features = _style_features(text)
    for key, value in features.items():
        if key == "self_ref":
            continue
        record = style.get(key) or {"counts": {}, "n": 0}
        counts = record.setdefault("counts", {})
        counts[value] = int(counts.get(value, 0)) + 1
        record["n"] = int(record.get("n", 0)) + 1
        style[key] = record
    style["self_ref"] = features["self_ref"]
    return result


def dominant(profile: dict[str, Any], key: str) -> tuple[str | None, float]:
    """Most frequent style value and its share."""
    record = ensure_profile(profile)["style"].get(key)
    if not isinstance(record, dict):
        return None, 0.0
    counts = record.get("counts") or {}
    total = sum(int(v) for v in counts.values())
    if total <= 0:
        return None, 0.0
    value = max(counts, key=lambda k: counts[k])
    return value, int(counts[value]) / total


def style_summary(profile: dict[str, Any]) -> dict[str, Any]:
    """Mirror at most one step away from neutral, and never the emotion."""
    out: dict[str, Any] = {}
    length, length_share = dominant(profile, "length")
    if length and length_share >= 0.65 and length != "medium":
        out["length"] = length
    formality, formality_share = dominant(profile, "formality")
    if formality and formality_share >= 0.65 and formality != "neutral":
        out["formality"] = formality
    emoji, emoji_share = dominant(profile, "emoji")
    if emoji == "frequent" and emoji_share >= 0.8:
        out["emoji"] = "sparse"  # never mirror more than a little
    mixed, mixed_share = dominant(profile, "mixed")
    if mixed == "high" and mixed_share >= 0.7:
        out["mixed"] = "high"
    return out


# --------------------------------------------------------------------------
# Model write path (P-1 .. P-4, P-9, P-10)
# --------------------------------------------------------------------------


def _clean_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return text[:limit]


def _normalise(text: str) -> str:
    return " ".join(text.split())


def _validate_value(key: str, value: Any) -> Any:
    kind, spec = SLOTS[key]
    if kind == "enum":
        text = str(value).strip()
        return text if text in spec else None
    if kind == "list":
        if isinstance(value, str):
            items = [value]
        elif isinstance(value, list):
            items = value
        else:
            return None
        cleaned = [_clean_text(item, 20) for item in items]
        out = [item for item in cleaned if item]
        return out[: int(spec)] or None
    if kind == "text":
        return _clean_text(value, int(spec))
    return None


def validate_delta(
    raw: Any, user_text: str, *, base_version: int | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Turn a model proposal into writes the server is willing to apply.

    Returns ``(clean, rejects)``. Every rejection carries a reason so the
    caller can log it — the rejects are the interesting half of this
    function, they are how P-2 and P-3 stay enforceable.
    """
    clean: dict[str, Any] = {"set": [], "note": [], "retire": []}
    rejects: list[dict[str, Any]] = []
    if not isinstance(raw, dict):
        return clean, [{"reason": "not_an_object"}]

    if base_version is not None and raw.get("base_version") is not None:
        try:
            if int(raw["base_version"]) != int(base_version):
                return clean, [{"reason": "version_mismatch"}]
        except (TypeError, ValueError):
            return clean, [{"reason": "bad_version"}]

    text = _normalise(user_text if isinstance(user_text, str) else "")
    accepted = 0

    def take() -> bool:
        nonlocal accepted
        if accepted >= MAX_DELTA:
            rejects.append({"reason": "over_limit"})
            return False
        return True

    def check_quote(quote: Any) -> str | None:
        if not isinstance(quote, str) or not quote.strip():
            rejects.append({"reason": "missing_quote"})
            return None
        quote = _normalise(quote)
        if len(quote) > MAX_QUOTE:
            rejects.append({"reason": "quote_too_long"})
            return None
        if quote not in text:
            rejects.append({"reason": "quote_mismatch"})
            return None
        if any(marker in quote for marker in META_INSTRUCTION_MARKERS):
            rejects.append({"reason": "meta_instruction"})
            return None
        return quote

    for item in raw.get("set") or []:
        if not isinstance(item, dict):
            rejects.append({"reason": "bad_item"})
            continue
        key = str(item.get("key", ""))
        if key not in SLOTS:
            rejects.append({"reason": "unknown_slot", "key": key})
            continue
        value = _validate_value(key, item.get("value"))
        if value is None:
            rejects.append({"reason": "bad_value", "key": key})
            continue
        quote = check_quote(item.get("quote"))
        if quote is None:
            continue
        if not take():
            break
        clean["set"].append(
            {
                "key": key,
                "value": value,
                "quote": quote,
                "confidence": str(item.get("confidence", "mid"))[:8],
            }
        )
        accepted += 1

    for item in raw.get("note") or []:
        if not isinstance(item, dict):
            rejects.append({"reason": "bad_item"})
            continue
        quote = check_quote(item.get("quote"))
        if quote is None:
            continue
        text_value = _clean_text(item.get("text"), int(SLOTS["note"][1]))
        if not text_value:
            rejects.append({"reason": "bad_value", "key": "note"})
            continue
        expire = _clean_text(item.get("expire"), 10)
        if not expire:
            rejects.append({"reason": "missing_expire", "key": "note"})
            continue
        if not take():
            break
        clean["note"].append({"text": text_value, "expire": expire, "quote": quote})
        accepted += 1

    for key in raw.get("retire") or []:
        if str(key) not in SLOTS:
            rejects.append({"reason": "unknown_slot", "key": str(key)})
            continue
        if not take():
            break
        clean["retire"].append(str(key))
        accepted += 1

    return clean, rejects


def apply_delta(
    profile: dict[str, Any],
    clean: dict[str, Any],
    *,
    turn: int,
    ts: float | None = None,
) -> dict[str, Any]:
    """Write validated deltas. They become visible from ``turn + 1`` (P-9)."""
    now = _now(ts)
    result = copy.deepcopy(ensure_profile(profile))
    slots = result["slots"]
    retired = result["retired"]

    for item in clean.get("set") or []:
        key = item["key"]
        slots[key] = {
            "value": item["value"],
            "quote": item["quote"],
            "confidence": item.get("confidence", "mid"),
            "turn": int(turn),
            "ts": now,
        }
        retired.pop(key, None)

    for item in clean.get("note") or []:
        slots["note"] = {
            "value": item["text"],
            "quote": item["quote"],
            "expire": item["expire"],
            "confidence": "mid",
            "turn": int(turn),
            "ts": now,
        }

    for key in clean.get("retire") or []:
        # P-6: retire, never delete. The quote and the date stay for audit.
        entry = slots.pop(key, {"value": None, "quote": "", "turn": int(turn)})
        entry["retired_at"] = now
        entry["retired_turn"] = int(turn)
        retired[key] = entry

    result["turn"] = max(int(result.get("turn", 0)), int(turn))
    result["version"] = int(result.get("version", 0)) + 1
    return result


# --------------------------------------------------------------------------
# Read path: prompt block, scheduling parameters, bet
# --------------------------------------------------------------------------


def _slot_text(key: str, entry: dict[str, Any]) -> str | None:
    value = entry.get("value")
    if key == "avoid":
        return f"{SLOT_LABELS[key]}={'/'.join(map(str, value))}后" if value else None
    if key == "block":
        return f"单块={value}分钟"
    if key == "buffer":
        return f"缓冲={value}分钟"
    if key == "note":
        expire = entry.get("expire")
        suffix = f"(至{expire[5:]})" if isinstance(expire, str) and len(expire) >= 10 else ""
        return f"备注={value}{suffix}"
    label = VALUE_LABELS.get(key, {}).get(str(value), str(value))
    return f"{SLOT_LABELS.get(key, key)}={label}"


def prompt_block(profile: dict[str, Any], turn: int | None = None) -> str:
    """The ``<persona>`` payload: parameters only, never labels (7.4)."""
    data = ensure_profile(profile)
    if turn is None:
        turn = int(data.get("turn", 0)) + 1
    parts: list[str] = []

    for key, entry in data["slots"].items():
        if not isinstance(entry, dict):
            continue
        if int(entry.get("turn", 0)) >= int(turn):
            continue  # P-9: written this turn, effective next turn
        piece = _slot_text(key, entry)
        if piece:
            parts.append(piece)

    level = expectation_level(data)
    if level != "neutral":
        parts.append(f"押注={LEVEL_LABELS[level]}")

    bias = shrunk(data, "estimate_bias")
    if abs(bias - 1.2) >= 0.15:
        parts.append(f"估时系数={bias}")

    best = _best_block(data)
    if best:
        parts.append(f"实测单块={best}分钟")

    style = style_summary(data)
    if style:
        words = []
        if style.get("length") == "short":
            words.append("短句")
        elif style.get("length") == "long":
            words.append("长句")
        if style.get("formality") == "casual":
            words.append("口语")
        if style.get("mixed") == "high":
            words.append("中英混用")
        if words:
            parts.append("风格=" + "/".join(words))

    text = "; ".join(parts)
    if len(text) > MAX_PROMPT_CHARS:
        text = text[: MAX_PROMPT_CHARS - 1] + "…"
    return text


def _best_block(profile: dict[str, Any], threshold: float = 0.6) -> str | None:
    data = ensure_profile(profile)
    scale = EXPECTATION_QUANTILE[expectation_level(data)]
    floor = min(threshold, 0.35 + scale * 0.35)
    for bucket in BLOCK_BUCKETS:
        rate = block_rate(data, bucket)
        if rate is not None and rate >= floor:
            return bucket
    return None


def scheduling_params(profile: dict[str, Any]) -> dict[str, Any]:
    """Derive the numbers the calendar uses. Never stored (7.1)."""
    data = ensure_profile(profile)
    level = expectation_level(data)
    bias = shrunk(data, "estimate_bias")

    slots = data["slots"]
    block = (slots.get("block") or {}).get("value") or _best_block(data) or "25"
    buffer = (slots.get("buffer") or {}).get("value") or (
        "15" if _interrupt_high(data) else "10"
    )
    day_ratio = max(0.6, min(1.0, 1.3 - bias * 0.4))
    avoid = (slots.get("avoid") or {}).get("value") or []

    return {
        "level": level,
        "quantile": EXPECTATION_QUANTILE[level],
        "block_minutes": int(block),
        "buffer_minutes": int(buffer),
        "daily_ratio": round(day_ratio, 3),
        "avoid_after": avoid,
        "estimate_bias": bias,
        "proactive": "L1",  # never granted by the profile (5.3, Q16)
    }


def _interrupt_high(profile: dict[str, Any]) -> bool:
    record = ensure_profile(profile)["indicators"].get("interrupt") or {}
    alpha, beta = (record.get("buckets") or {}).get("high", [0, 0])
    total = alpha + beta
    return total >= 3 and alpha / total >= 0.6


def bet(profile: dict[str, Any], predicted: float) -> dict[str, Any]:
    """Prediction stays honest; the bet is what the user is offered (7.8)."""
    level = expectation_level(profile)
    predicted = max(0.0, min(1.0, float(predicted)))
    offered = max(0.05, min(0.95, predicted + BET_DELTA[level]))
    return {
        "predicted": round(predicted, 3),
        "bet": round(offered, 3),
        "level": level,
        "differs": abs(offered - predicted) > 1e-9,
    }


# --------------------------------------------------------------------------
# Retention, purge, merge
# --------------------------------------------------------------------------


def slot_weight(profile: dict[str, Any], key: str, *, now: float | None = None) -> float:
    entry = ensure_profile(profile)["slots"].get(key) or {}
    stamp = entry.get("ts")
    if not isinstance(stamp, (int, float)):
        return 1.0
    return round(_weight(float(stamp), _now(now), TAU_DAYS), 4)


def purge_candidates(
    profile: dict[str, Any],
    *,
    now: float | None = None,
    rejections: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """The only legal reasons to clear a row (7.10)."""
    data = ensure_profile(profile)
    moment = _now(now)
    out: list[dict[str, Any]] = []

    for key in data["slots"]:
        if slot_weight(data, key, now=moment) < PURGE_WEIGHT:
            out.append({"key": key, "reason": "decayed", "weight": slot_weight(data, key, now=moment)})

    for key, count in (rejections or {}).items():
        if count >= 2:
            out.append({"key": key, "reason": "rejected_twice", "count": count})

    for key, record in data["indicators"].items():
        if record.get("state") == "drift" and record.get("drift_at"):
            out.append({"key": key, "reason": "drift_review", "state": "drift"})

    return out


def merge(local: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    """Per-key last-write-wins across devices (P-10).

    The profile with the higher ``version`` wins wholesale; when both
    sides moved, slots merge per key by write time so one device cannot
    roll the other back.
    """
    left = ensure_profile(local)
    right = ensure_profile(remote)
    if int(left.get("version", 0)) >= int(right.get("version", 0)):
        base, other = left, right
    else:
        base, other = right, left

    result = copy.deepcopy(base)
    for key, entry in (other.get("slots") or {}).items():
        if not isinstance(entry, dict):
            continue
        mine = (result["slots"] or {}).get(key) or {}
        if int(entry.get("turn", 0)) > int(mine.get("turn", 0)):
            result["slots"][key] = entry
    result["version"] = max(
        int(left.get("version", 0)), int(right.get("version", 0))
    ) + 1
    return result


def to_json(profile: dict[str, Any]) -> str:
    return json.dumps(ensure_profile(profile), ensure_ascii=False, separators=(",", ":"))

def from_json(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as error:
        raise ProfileError(f"profile is not valid JSON: {error}") from error
    return ensure_profile(payload)


def advance(
    profile: dict[str, Any],
    *,
    user_text: str,
    raw_delta: Any,
    turn: int,
    ts: float | None = None,
) -> dict[str, Any]:
    """One conversation turn: validate, apply, and build the next prompt.

    This is the only function the request handler needs to call. An empty
    delta is a no-op (P-0): the profile and its version stay untouched, so
    "most turns change nothing" is visible in the record.
    """
    base = ensure_profile(profile)
    clean, rejects = validate_delta(
        raw_delta, user_text, base_version=int(base.get("version", 0))
    )
    if not (clean["set"] or clean["note"] or clean["retire"]):
        return {
            "profile": base,
            "prompt": prompt_block(base, turn + 1),
            "applied": {"set": [], "note": [], "retire": []},
            "rejected": rejects,
            "changed": False,
        }
    updated = apply_delta(base, clean, turn=turn, ts=ts)
    return {
        "profile": updated,
        "prompt": prompt_block(updated, turn + 1),
        "applied": clean,
        "rejected": rejects,
        "changed": True,
    }
