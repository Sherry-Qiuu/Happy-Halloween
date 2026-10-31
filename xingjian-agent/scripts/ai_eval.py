#!/usr/bin/env python3
"""Offline gate for the AI pipeline: protocol, policy, profile, budget.

    python scripts/ai_eval.py
    python scripts/ai_eval.py --json out/ai_eval.json

No network, no API key, no sleeping: every case runs against the same
functions the server calls, so a regression in ``sanitize_changes``,
``policy.apply`` or ``sched_profile.validate_delta`` fails here before
it reaches a user.

Exit code 0 means every suite passed its gate. Rules and budgets are
described in ``docs/ai-runtime-spec.md``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import deepseek as ds  # noqa: E402
import policy  # noqa: E402
import prompts  # noqa: E402
import protocol  # noqa: E402
import sched_profile as sp  # noqa: E402
import server  # noqa: E402

GOALS = 6
GATE_PROTOCOL = 1.0
GATE_POLICY = 1.0
GATE_PROFILE = 1.0
GATE_LOCAL_P95_MS = 8.0
GATE_REPLAY_SECONDS = 2.0
LATENCY_ROUNDS = 60
LATENCY_CHUNK = 7

EMOJI_RANGES = ((0x1F300, 0x1FAFF), (0x2600, 0x27BF), (0x1F000, 0x1F0FF))


def has_emoji(text: str) -> bool:
    """True when the text still carries a pictograph."""
    for char in text:
        point = ord(char)
        if any(low <= point <= high for low, high in EMOJI_RANGES):
            return True
    return False


class Suite:
    """Collect cases for one gate and remember the first failure."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.cases: list[dict[str, Any]] = []

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        self.cases.append({"case": label, "ok": bool(ok), "detail": detail})

    @property
    def passed(self) -> int:
        return sum(1 for case in self.cases if case["ok"])

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def score(self) -> float:
        return self.passed / self.total if self.total else 0.0


GOOD_CHANGES = [
    {
        "type": "move_event",
        "date": "2026-11-03",
        "title": "专注块",
        "start": "09:30",
        "duration": 45,
    },
    {
        "type": "add_event",
        "date": "2026-11-03",
        "title": "临时会议",
        "start": "14:00",
        "duration": "60",
    },
    {
        "type": "remove_event",
        "date": "2026-11-03",
        "title": "客户会",
    },
    {
        "type": "add_task",
        "title": "写周报",
        "minutes": 30,
    },
    {
        "type": "add_next_action",
        "text": "先写大纲",
        "minutes": 20,
        "goal_index": 2,
    },
    {
        "type": "log_actual",
        "date": "2026-11-02",
        "title": "阅读",
        "start": "11:00",
        "duration": 25,
    },
]

BAD_CHANGES = [
    (
        "未知动作",
        {"type": "reorder_week", "date": "2026-11-03", "title": "整周重排"},
    ),
    (
        "时间越界",
        {
            "type": "move_event",
            "date": "2026-11-03",
            "title": "专注块",
            "start": "25:99",
            "duration": 30,
        },
    ),
    (
        "时长为零",
        {
            "type": "add_event",
            "date": "2026-11-03",
            "title": "空块",
            "start": "10:00",
            "duration": 0,
        },
    ),
    (
        "时长超上限",
        {
            "type": "add_event",
            "date": "2026-11-03",
            "title": "超长块",
            "start": "10:00",
            "duration": 999,
        },
    ),
    (
        "缺标题",
        {
            "type": "move_event",
            "date": "2026-11-03",
            "start": "10:00",
            "duration": 30,
        },
    ),
    (
        "目标下标越界",
        {
            "type": "add_next_action",
            "text": "写提纲",
            "minutes": 20,
            "goal_index": 99,
        },
    ),
    ("不是对象", "move_event"),
]


def suite_protocol() -> Suite:
    """Every model-proposed change must survive or die by a known rule."""
    suite = Suite("协议")

    for change in GOOD_CHANGES:
        kind = change["type"]
        cleaned = protocol.sanitize_changes([change], GOALS)
        suite.check(
            f"接受 {kind}",
            len(cleaned) == 1 and cleaned[0]["type"] == kind,
            f"got={cleaned}",
        )

    capped = protocol.sanitize_changes(GOOD_CHANGES * 2, GOALS)
    suite.check(
        "单轮上限",
        len(capped) == protocol.MAX_CHANGES,
        f"got={len(capped)}",
    )

    for label, change in BAD_CHANGES:
        cleaned = protocol.sanitize_changes([change], GOALS)
        suite.check(f"拒绝 {label}", cleaned == [], f"got={cleaned}")

    suite.check(
        "拒绝非列表",
        protocol.sanitize_changes({"type": "move_event"}, GOALS) == [],
    )
    return suite


def suite_policy() -> Suite:
    """Tone rules, facts and crisis handling are code, not prompt hopes."""
    suite = Suite("策略")

    raw = "今天安排得不错😀🎉"
    cleaned, violations = policy.apply(raw)
    flagged = any(v["rule"] == "emoji" for v in violations)
    suite.check(
        "去 emoji",
        not has_emoji(cleaned) and flagged,
        f"out={cleaned!r}",
    )

    cleaned, _ = policy.apply("先把周报写完！")
    suite.check("感叹号转句号", "！" not in cleaned and "。" in cleaned, cleaned)

    cleaned, _ = policy.apply("一。二。三。四。五。")
    suite.check(
        "正文三句封顶",
        cleaned.count("。") <= policy.MAX_SENTENCES,
        f"out={cleaned!r}",
    )

    cleaned, _ = policy.apply("你已经很棒了，加油。")
    suite.check("鸡汤被丢弃", "加油" not in cleaned, cleaned)

    cleaned, _ = policy.apply("你又超时了，实际 52 分钟。")
    suite.check(
        "判断与事实分句处理",
        "52" in cleaned and "你又" not in cleaned,
        cleaned,
    )

    cleaned, violations = policy.apply("你又超时了 52 分钟。")
    suite.check(
        "带数字的分句保留",
        "52" in cleaned,
        cleaned,
    )
    suite.check(
        "保留时记违规",
        any(v.get("kept") == "carries_fact" for v in violations),
        f"{violations}",
    )

    suite.check("识别危机", policy.detect_crisis("我不想活了"))
    crisis, _ = policy.apply("我不想活了", crisis=True)
    suite.check("危机替换为固定文案", crisis == policy.CRISIS_REPLY, crisis)

    report, _ = policy.apply("第一段观察。\n\n第二段观察。", max_sentences=None)
    suite.check("报告保留段落", "\n\n" in report and "第二段" in report, report)

    draft = policy.validate_draft({"facts": ["实际用了 52 分钟"]})
    suite.check("草稿可校验", draft is not None, f"{draft}")
    surface, added = policy.append_missing("今天做了不少事。", ["实际用了 52 分钟"])
    suite.check("缺少事实时补记", "52 分钟" in surface and added, surface)

    text = "把英语练习挪到 16:30，其余不动"
    suite.check(
        "分片无损",
        "".join(policy.chunks(text)) == text,
        f"{list(policy.chunks(text))}",
    )
    return suite


def suite_profile() -> Suite:
    """The model may write the profile only through a validated delta."""
    suite = Suite("画像")
    profile = sp.empty_profile()

    user_text = "我一般专注二十五分钟，早上效率高，中间要缓冲十分钟"
    good = {
        "set": [
            {"key": "block", "value": "25", "quote": "专注二十五分钟"},
            {"key": "rhythm", "value": "morning", "quote": "早上效率高"},
        ]
    }
    clean, rejects = sp.validate_delta(good, user_text)
    suite.check(
        "接受带原话的写入",
        len(clean["set"]) == 2 and not rejects,
        f"clean={clean} rejects={rejects}",
    )

    fabricated = {
        "set": [{"key": "density", "value": "dense", "quote": "我喜欢排得很满"}]
    }
    clean, rejects = sp.validate_delta(fabricated, user_text)
    suite.check("拒绝编造原话", not clean["set"] and rejects, f"{rejects}")

    meta = {
        "set": [{"key": "block", "value": "90", "quote": "记住以后都"}]
    }
    clean, rejects = sp.validate_delta(meta, "记住以后都按九十分钟排")
    suite.check("拒绝元指令", not clean["set"] and rejects, f"{rejects}")

    four = {
        "set": [
            {"key": "block", "value": "25", "quote": "专注二十五分钟"},
            {"key": "rhythm", "value": "morning", "quote": "早上效率高"},
            {"key": "buffer", "value": "10", "quote": "缓冲十分钟"},
            {"key": "density", "value": "sparse", "quote": "中间要缓冲"},
        ]
    }
    clean, _ = sp.validate_delta(four, user_text)
    suite.check(
        "单轮写入上限",
        len(clean["set"]) <= sp.MAX_DELTA,
        f"got={len(clean['set'])}",
    )

    clean, rejects = sp.validate_delta(
        {"base_version": 999, "set": [
            {"key": "block", "value": "15", "quote": "专注二十五分钟"}
        ]},
        user_text,
        base_version=int(profile["version"]),
    )
    suite.check(
        "版本不一致整体拒绝",
        not clean["set"] and any(
            r.get("reason") == "version_mismatch" for r in rejects
        ),
        f"{rejects}",
    )

    result = sp.advance(profile, user_text="今天有点乱", raw_delta={}, turn=1)
    suite.check(
        "空增量是空操作",
        result["changed"] is False
        and result["profile"]["version"] == profile["version"],
        f"changed={result['changed']}",
    )

    applied = sp.advance(profile, user_text=user_text, raw_delta=good, turn=1)
    persona = applied["prompt"]
    suite.check(
        "画像提示词有上限",
        len(persona) <= sp.MAX_PROMPT_CHARS,
        f"len={len(persona)}",
    )
    suite.check("提示词只给参数", "单块=" in persona, persona)
    suite.check(
        "提示词不含类型标签",
        not any(label in persona for label in ("乐观估时型", "稳定节奏型")),
        persona,
    )
    return suite


def _reply_text() -> str:
    """One realistic turn: prose, then a fenced proposal block."""
    prose = "先把英语练习挪到 16:30，上午的专注块不需要动。"
    block = {
        "summary": "把英语练习挪到 16:30，其余不动",
        "changes": [GOOD_CHANGES[0]],
    }
    raw = json.dumps(block, ensure_ascii=False)
    return f"{prose}\n\n{prompts.PROPOSAL_START}{raw}{prompts.PROPOSAL_END}"


def suite_budget() -> tuple[Suite, dict[str, Any]]:
    """Context, replay and local overhead stay inside the budget."""
    suite = Suite("预算")
    metrics: dict[str, Any] = {}

    big = {"blob": "x" * 20000}
    block = prompts.state_block(big)
    suite.check(
        "状态块截断",
        len(block) <= prompts.MAX_STATE_CHARS + 30
        and "已截断" in block,
        f"len={len(block)}",
    )

    history = [
        {"role": "user", "content": "上午临时开会"},
        {"role": "assistant", "content": "收到"},
    ]
    with_persona = prompts.chat_messages(history, {"tasks": []}, "单块=25分钟")
    plain = prompts.chat_messages(history, {"tasks": []})
    suite.check(
        "三段系统提示",
        [m["role"] for m in with_persona].count("system") == 3,
        f"{[m['role'] for m in with_persona]}",
    )
    suite.check(
        "无画像时两段",
        [m["role"] for m in plain].count("system") == 2,
    )
    suite.check(
        "历史条数不变",
        len(with_persona) == 3 + len(history),
        f"{len(with_persona)}",
    )

    text = _reply_text()
    prose = text.split(prompts.PROPOSAL_START)[0].strip()
    elapsed: list[float] = []
    for _ in range(LATENCY_ROUNDS):
        splitter = ds.ProposalSplitter()
        started = time.perf_counter()
        for index in range(0, len(text), LATENCY_CHUNK):
            splitter.feed(text[index:index + LATENCY_CHUNK])
        splitter.finish()
        policy.apply(prose, max_sentences=3)
        ds.parse_turn(splitter.block, GOALS)
        elapsed.append((time.perf_counter() - started) * 1000)

    elapsed.sort()
    p50 = elapsed[len(elapsed) // 2]
    p95 = elapsed[math.ceil(0.95 * len(elapsed)) - 1]
    metrics["local_p50_ms"] = round(p50, 3)
    metrics["local_p95_ms"] = round(p95, 3)
    suite.check(
        "本地开销 p95",
        p95 <= GATE_LOCAL_P95_MS,
        f"{p95:.2f} ms / 门限 {GATE_LOCAL_P95_MS} ms",
    )

    splitter = ds.ProposalSplitter()
    splitter.feed(text)
    splitter.finish()
    proposal, _, _ = ds.parse_turn(splitter.block, GOALS)
    suite.check("提案块可解析", proposal is not None, f"{proposal}")

    clean, _ = policy.apply(
        text.split(prompts.PROPOSAL_START)[0].strip()
    )
    pieces = math.ceil(len(clean) / policy.CHUNK_CHARS)
    replay = pieces * server.REPLAY_DELAY
    metrics["replay_seconds"] = round(replay, 3)
    metrics["replay_chars"] = len(clean)
    suite.check(
        "回放时长",
        replay <= GATE_REPLAY_SECONDS,
        f"{replay:.2f} s / 门限 {GATE_REPLAY_SECONDS} s",
    )
    metrics["state_chars"] = prompts.MAX_STATE_CHARS
    metrics["max_changes"] = protocol.MAX_CHANGES
    metrics["max_delta"] = sp.MAX_DELTA
    metrics["max_sentences"] = policy.MAX_SENTENCES
    return suite, metrics


def _report(suites: list[Suite], metrics: dict[str, Any]) -> int:
    """Print the human report and return the process exit code."""
    print("行间 · AI 运行评测（离线，不调用上游）")
    print("-" * 46)
    gates = {
        "协议": (GATE_PROTOCOL, suites[0].score),
        "策略": (GATE_POLICY, suites[1].score),
        "画像": (GATE_PROFILE, suites[2].score),
    }
    failed = 0
    for suite in suites:
        ok = True
        if suite.name in gates:
            gate, score = gates[suite.name]
            ok = score >= gate
        elif suite.name == "预算":
            ok = all(case["ok"] for case in suite.cases)
        mark = "✓" if ok else "✗"
        print(f"[{suite.name}] {suite.passed}/{suite.total} {mark}")
        for case in suite.cases:
            if not case["ok"]:
                print(f"    - {case['case']}: {case['detail']}")
        failed += 0 if ok else 1

    if metrics:
        print(
            "本地开销 p50/p95 = "
            f"{metrics['local_p50_ms']} / {metrics['local_p95_ms']} ms"
        )
        print(
            f"回放 {metrics['replay_seconds']} s"
            f"（{metrics['replay_chars']} 字 / {policy.CHUNK_CHARS} 字一片）"
        )
    print("-" * 46)
    print("闸门：" + ("通过" if not failed else f"{failed} 项未通过"))
    return 0 if not failed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline AI pipeline gate")
    parser.add_argument(
        "--json",
        metavar="PATH",
        help="also write the report as JSON",
    )
    args = parser.parse_args(argv)

    suites = [suite_protocol(), suite_policy(), suite_profile()]
    budget, metrics = suite_budget()
    suites.append(budget)

    code = _report(suites, metrics)
    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pass": code == 0,
            "metrics": metrics,
            "suites": [
                {
                    "name": suite.name,
                    "passed": suite.passed,
                    "total": suite.total,
                    "cases": suite.cases,
                }
                for suite in suites
            ],
        }
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"报告已写入 {target}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
