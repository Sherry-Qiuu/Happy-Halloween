"""Walk one user through a few turns of the scheduling profile.

    python scripts/profile_demo.py

Prints what the server would store and what the model would see. No
network, no API key: this is the arithmetic of ``sched_profile`` only.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sched_profile as sp  # noqa: E402

DAY = 86400.0
BASE = 1_700_000_000.0


def line(title: str) -> None:
    print(f"\n{'=' * 4} {title} {'=' * 4}")


def main() -> int:
    profile = sp.empty_profile()

    line("第 1 轮：用户首次说自己的节奏")
    text = "我一般只能专注二十五分钟，早上效率高"
    delta = {
        "set": [
            {"key": "block", "value": "25", "quote": "只能专注二十五分钟"},
            {"key": "rhythm", "value": "morning", "quote": "早上效率高"},
            # 模型顺手编了一条用户没说过的话
            {"key": "density", "value": "dense", "quote": "我喜欢排得很满"},
        ]
    }
    result = sp.advance(profile, user_text=text, raw_delta=delta, turn=1, ts=BASE)
    profile = result["profile"]
    print("接受:", [item["key"] for item in result["applied"]["set"]])
    print("拒绝:", [item["reason"] for item in result["rejected"]])
    print("下一轮模型看到:", result["prompt"])

    line("第 2 轮：模型不改任何画像（大多数轮次就该是这样）")
    result = sp.advance(profile, user_text="今天有点乱", raw_delta={}, turn=2, ts=BASE + 1)
    print("changed =", result["changed"], "version =", result["profile"]["version"])

    line("第 3 轮：日历回填一次成功的 25 分钟块")
    profile, outcome = sp.observe(
        profile, "block_success", True, bucket="25", ts=BASE + 2
    )
    print("block_success(25) =", sp.block_rate(profile, "25"))
    print("排布参数 =", sp.scheduling_params(profile))

    line("第 4 轮：估时连续三次偏高（漂移，不是噪声）")
    for index in range(5):
        profile, _ = sp.observe(
            profile, "estimate_bias", 1.5, ts=BASE - 30 * DAY + index
        )
    print("旧中位数 =", profile["indicators"]["estimate_bias"]["median"])
    for offset in range(3):
        profile, outcome = sp.observe(
            profile, "estimate_bias", 3.5, ts=BASE + 10 + offset
        )
    record = profile["indicators"]["estimate_bias"]
    print("三次之后:", outcome["reason"], "状态 =", record["state"],
          "中位数 =", record["median"], "样本 =", record["n"])

    line("第 5 轮：同一个预测，三种姿态的押注")
    for level in sp.EXPECTATION_LEVELS:
        view = dict(profile)
        view["expectation"] = dict(profile["expectation"], level=level)
        bet = sp.bet(view, 0.6)
        print(f"{sp.LEVEL_LABELS[level]:>2}  预测 {bet['predicted']} -> 押注 {bet['bet']}")

    line("第 6 轮：模型看到的东西")
    print(sp.prompt_block(profile, turn=99) or "(空)")

    line("第 7 轮：用户否定后，旧的写法只失效、不删除")
    profile = sp.apply_delta(profile, {"retire": ["rhythm"]}, turn=6, ts=BASE + 20)
    print("slots:", sorted(profile["slots"]))
    print("retired:", sorted(profile["retired"]))
    print("保留的原话:", profile["retired"]["rhythm"]["quote"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
