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
_REPEAT_PUNCT = re.compile(r"([？?!！。~～])\1+")

_EMOJI_RANGES = ((0x1F300, 0x1FAFF), (0x2600, 0x27BF), (0x1F000, 0x1F0FF))

# --------------------------------------------------------------------------
# 口语上限（硬约束）：可以跟着用户的口吻走，但不许滑成网感或黑话。
# --------------------------------------------------------------------------

MAX_PARTICLE_RATIO = 0.12  # 语气词占正文比例上限（够长才算数）
MAX_PARTICLES_PER_CLAUSE = 1
PARTICLE_FLOOR = 4  # 短句里的两三个语气词不算"过分口语"

PARTICLES = ("啊", "吧", "嘛", "呢", "呀", "啦", "哦", "呗", "嗯", "哈")

# 网络用语与粗俗表达：直接删，不做替换（替换容易留下更怪的句子）
COLLOQUIAL_SLANG = (
    "绝绝子",
    "yyds",
    "YYDS",
    "破防",
    "栓Q",
    "狠狠",
    "麻了",
    "绷不住",
    "蚌埠住了",
    "awsl",
    "xswl",
    "逆天",
    "离谱",
    "牛逼",
    "牛批",
    "卧槽",
    "特么",
    "妈的",
    "搞毛",
    "笑死",
    "233",
    "emo了",
)

# 职场黑话：换成平实说法（用户没这么说话，AI 就不该这么说话）
JARGON = {
    "赋能": "帮",
    "闭环": "完整流程",
    "抓手": "切入点",
    "颗粒度": "细到什么程度",
    "对齐": "确认",
    "沉淀": "留下来",
    "链路": "环节",
    "心智": "想法",
    "打法": "做法",
    "底层逻辑": "原因",
    "顶层设计": "整体安排",
    "势能": "动力",
}

# 公文体：整句删掉比换成半截话更安全
FORMAL_STALE = ("综上所述", "特此", "予以", "请知悉", "悉知", "兹")


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


def _temper(piece: str) -> tuple[str, list[dict[str, Any]]]:
    """Trim a clause to the allowed register: not slang, not corporate.

    Returns the clause plus what was removed, so the log shows why a reply
    differs from what the model wrote.
    """
    out = piece
    violations: list[dict[str, Any]] = []

    for word in COLLOQUIAL_SLANG:
        if word in out:
            out = out.replace(word, "")
            violations.append({"rule": "too_colloquial", "word": word})

    for word, plain in JARGON.items():
        if word in out:
            out = out.replace(word, plain)
            violations.append({"rule": "jargon", "word": word})

    for word in FORMAL_STALE:
        if word in out:
            out = out.replace(word, "")
            violations.append({"rule": "stale_formal", "word": word})

    before = out
    out = _REPEAT_PUNCT.sub(lambda match: match.group(1), out)
    if out != before:
        violations.append({"rule": "repeat_punct"})

    # At most one particle per clause: "好呀好呀呀" is a mood, not a sentence.
    seen = 0
    thinned: list[str] = []
    for char in out:
        if char in PARTICLES:
            seen += 1
            if seen > MAX_PARTICLES_PER_CLAUSE:
                continue
        thinned.append(char)
    if thinned != list(out):
        out = "".join(thinned)
        violations.append({"rule": "too_colloquial", "detail": "particles"})

    return out.strip(" ，、~"), violations


def particle_ratio(text: str) -> float:
    """Share of particles in the reply, used as the hard colloquial ceiling."""
    if not text:
        return 0.0
    count = sum(1 for char in text if char in PARTICLES)
    return count / len(text)


def _strip_particles(text: str) -> str:
    return "".join(char for char in text if char not in PARTICLES)


def _tidy(text: str) -> str:
    """Remove the punctuation debris that filtering leaves behind."""
    text = re.sub(r"[，、]\s*[。]", "。", text)
    text = re.sub(r"([，、])\1+", r"\1", text)
    text = re.sub(r"(^|[。\n])\s*[，、]+", r"\1", text)
    text = re.sub(r"[，、]+\s*$", "", text)
    return text.strip()


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

    deduped = _REPEAT_PUNCT.sub(lambda match: match.group(1), cleaned)
    if deduped != cleaned:
        violations.append({"rule": "repeat_punct"})
        cleaned = deduped

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
                if hit is not None and not _carries_fact(piece):
                    violations.append(
                        {"rule": hit, "clause": piece, "kept": False}
                    )
                    continue
                if hit is not None:
                    # A fact outranks a tone rule: keep it, record the smell.
                    violations.append(
                        {"rule": hit, "clause": piece, "kept": "carries_fact"}
                    )
                piece, tone = _temper(piece)
                violations.extend(tone)
                if piece:
                    clauses.append(piece)
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
    # Hard ceiling: mirroring the user's wording stops short of sounding
    # like a feed comment. Short replies are judged per clause only.
    particles = sum(1 for char in body if char in PARTICLES)
    if particles >= PARTICLE_FLOOR and particle_ratio(body) > MAX_PARTICLE_RATIO:
        body = _strip_particles(body)
        violations.append({"rule": "too_colloquial", "detail": "density"})
    body = _tidy(body)
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
