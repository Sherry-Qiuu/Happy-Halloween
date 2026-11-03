"""Output policy: the half of the rules a prompt cannot guarantee.

``用户画像类型与排布策略.md`` §5.6 (positive framing) and §5.7 (think one
way, speak another) are tone rules, so a prompt can only nudge them. This
module makes them mechanical:

* mechanical filters (emoji, exclamation marks, sentence count) always run;
* sentences that carry evaluation but no fact are dropped, sentences that
  carry a number are kept — a fact outranks a tone rule;
* a draft payload supplies the facts the surface must contain and the
  forecast that must stay honest;
* crisis text bypasses the positive framing entirely.

Nothing here calls the network or the model. Standard library only.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Iterator

MAX_SENTENCES = 3
MAX_FACTS = 3
MAX_FACT_CHARS = 40
CHUNK_CHARS = 4

# --------------------------------------------------------------------------
# Crisis: the one case where "stay positive" would be negligent.
# --------------------------------------------------------------------------

CRISIS_MARKERS = (
    "自杀",
    "自残",
    "轻生",
    "不想活",
    "活着没意思",
    "死了算了",
    "割腕",
    "跳楼",
    "结束生命",
    "安眠药",
    "想消失",
    "没脸活",
)

CRISIS_REPLY = (
    "我听到你现在很难受，这不是你的错。\n\n"
    "现在最要紧的只有一件事：找到能立刻到你身边的人。"
    "如果在中国大陆，可以拨 120 或 110，"
    "也可以拨心理援助热线 400-161-9995，24 小时有人接。\n\n"
    "我可以继续陪你。先告诉我一句：你现在身边有人吗？"
)

# --------------------------------------------------------------------------
# Banned phrasing. Each entry is (needle, rule name).
# --------------------------------------------------------------------------

BANNED: tuple[tuple[str, str], ...] = (
    # 虚假承诺：不许诺结果
    ("一定可以", "false_hope"),
    ("一定能", "false_hope"),
    ("肯定能", "false_hope"),
    ("绝对能", "false_hope"),
    ("保证能", "false_hope"),
    ("你一定会", "false_hope"),
    # 鸡汤：情绪替代行动
    ("加油", "chicken_soup"),
    ("相信自己", "chicken_soup"),
    ("你很棒", "chicken_soup"),
    ("坚持就是胜利", "chicken_soup"),
    ("别放弃", "chicken_soup"),
    ("一切都会好的", "chicken_soup"),
    ("你会好起来的", "chicken_soup"),
    # 冷话：评价人，而不是陈述事
    ("你又", "cold_talk"),
    ("估计不准", "cold_talk"),
    ("你就是", "cold_talk"),
    ("不够努力", "cold_talk"),
    ("太懒", "cold_talk"),
    ("拖沓", "cold_talk"),
    # 比较式安慰：否定他的处境
    ("比你更惨", "comparison"),
    ("有人比你", "comparison"),
    ("至少你还有", "comparison"),
    ("别人都", "comparison"),
    # 否定感受
    ("别想太多", "invalidation"),
    ("想开点", "invalidation"),
    ("这没什么", "invalidation"),
    ("别难过", "invalidation"),
    # 伪造低评价
    ("你不行", "fake_doubt"),
    ("你做不到", "fake_doubt"),
    ("你没希望", "fake_doubt"),
    ("你不适合", "fake_doubt"),
)

_SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n]+")
_CLAUSE_SPLIT = re.compile(r"[，,、]")
_HAS_DIGIT = re.compile(r"\d")

_EMOJI_RANGES = ((0x1F300, 0x1FAFF), (0x2600, 0x27BF), (0x1F000, 0x1F0FF))


def _strip_emoji(text: str) -> tuple[str, int]:
    kept: list[str] = []
    removed = 0
    for char in text:
        code = ord(char)
        if any(low <= code <= high for low, high in _EMOJI_RANGES):
            removed += 1
            continue
        if char == "\ufe0f":
            removed += 1
            continue
        kept.append(char)
    return "".join(kept), removed


def _carries_fact(sentence: str) -> bool:
    return bool(_HAS_DIGIT.search(sentence)) or "分钟" in sentence


def _split(text: str) -> list[str]:
    return [piece.strip() for piece in _SENTENCE_SPLIT.split(text) if piece.strip()]


def detect_crisis(text: str) -> bool:
    """True when the user text asks for help, not for scheduling."""
    if not isinstance(text, str):
        return False
    return any(marker in text for marker in CRISIS_MARKERS)


def apply(
    text: str,
    *,
    max_sentences: int | None = MAX_SENTENCES,
    crisis: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    """Return the text that is allowed to reach the user, plus violations.

    The violations are the useful half: they name the rule, the sentence
    and whether the sentence survived because it carried a fact.
    """
    if crisis:
        return CRISIS_REPLY, [{"rule": "crisis_override", "sentence": ""}]

    if not isinstance(text, str) or not text.strip():
        return "", []

    violations: list[dict[str, Any]] = []
    cleaned, removed_emoji = _strip_emoji(text)
    if removed_emoji:
        violations.append({"rule": "emoji", "count": removed_emoji})

    if "！" in cleaned or "!" in cleaned:
        violations.append({"rule": "exclamation"})
        cleaned = cleaned.replace("！", "。").replace("!", "。")

    # Work paragraph by paragraph so the reflection report keeps its shape;
    # work clause by clause so "你又超时了，实际 52 分钟" loses the judgement
    # and keeps the number instead of trading one for the other.
    kept: list[tuple[int, str]] = []
    for index, paragraph in enumerate(re.split(r"\n\s*\n", cleaned)):
        for sentence in _split(paragraph):
            clauses: list[str] = []
            for clause in _CLAUSE_SPLIT.split(sentence):
                piece = clause.strip()
                if not piece:
                    continue
                hit = next(
                    (rule for needle, rule in BANNED if needle in piece), None
                )
                if hit is None:
                    clauses.append(piece)
                elif _carries_fact(piece):
                    # A fact outranks a tone rule: keep it, record the smell.
                    violations.append(
                        {"rule": hit, "clause": piece, "kept": "carries_fact"}
                    )
                    clauses.append(piece)
                else:
                    violations.append(
                        {"rule": hit, "clause": piece, "kept": False}
                    )
            if clauses:
                kept.append((index, "，".join(clauses)))

    dropped = 0
    if max_sentences is not None and len(kept) > max_sentences:
        dropped = len(kept) - max_sentences
        kept = kept[:max_sentences]
        violations.append({"rule": "too_long", "count": dropped})

    rendered: list[str] = []
    paragraph_index = -1
    for index, sentence in kept:
        if index != paragraph_index:
            rendered.append(sentence)
            paragraph_index = index
        else:
            rendered[-1] += "。" + sentence

    body = "\n\n".join(rendered)
    if body and not body.endswith(("？", "。", "…")):
        body += "。"
    # A question keeps its mark: splitting on punctuation would otherwise
    # turn "行吗？" into a statement.
    if body.endswith("。") and cleaned.rstrip().endswith(("？", "?")):
        body = body[:-1] + "？"
    return body, violations


def chunks(text: str, size: int = CHUNK_CHARS) -> Iterator[str]:
    """Replay the sanitized text in small pieces so the UI still types."""
    if not text:
        return
    for index in range(0, len(text), max(1, size)):
        yield text[index:index + max(1, size)]


# --------------------------------------------------------------------------
# Draft payload (5.7): what the model thinks, never what the user reads.
# --------------------------------------------------------------------------


def validate_draft(raw: Any) -> dict[str, Any] | None:
    """Keep only the parts of the draft that are checkable."""
    if not isinstance(raw, dict):
        return None
    draft: dict[str, Any] = {}

    predicted = raw.get("predicted")
    if isinstance(predicted, (int, float)) and 0.0 <= float(predicted) <= 1.0:
        draft["predicted"] = round(float(predicted), 3)

    facts: list[str] = []
    for item in raw.get("facts") or []:
        if not isinstance(item, str):
            continue
        fact = " ".join(item.split())[:MAX_FACT_CHARS]
        if fact and _HAS_DIGIT.search(fact):
            facts.append(fact)
        if len(facts) >= MAX_FACTS:
            break
    if facts:
        draft["facts"] = facts

    risk = raw.get("risk")
    if isinstance(risk, str) and risk.strip():
        draft["risk"] = " ".join(risk.split())[:120]

    return draft or None


def missing_facts(draft: dict[str, Any] | None, surface: str) -> list[str]:
    """Facts the draft promised to say, but the reply left out."""
    if not isinstance(draft, dict):
        return []
    out: list[str] = []
    for fact in draft.get("facts") or []:
        if not _HAS_DIGIT.search(fact):
            continue
        if not any(
            run in surface for run in re.findall(r"\d+(?:\.\d+)?", fact)
        ):
            out.append(fact)
    return out


def append_missing(surface: str, facts: Iterable[str]) -> tuple[str, list[str]]:
    """Add omitted facts as a neutral footnote instead of rewriting prose."""
    missing = [fact for fact in facts if fact]
    if not missing:
        return surface, []
    tail = "；".join(missing)
    body = surface.rstrip()
    if body and not body.endswith(("。", "？", "…")):
        body += "。"
    return f"{body}\n\n记录：{tail}。", missing
