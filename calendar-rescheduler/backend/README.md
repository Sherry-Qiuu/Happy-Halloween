# rescheduler

快速、画像感知的日历重排策略。给定未来 7 天的事件、一次变化和用户画像，返回一个可解释的提案（最多 4 条协议兼容改动 + 账单 + 备选）。

- 设计说明：[STRATEGY.md](STRATEGY.md)
- 核心：[core.py](core.py)
- 基准：[evaluate.py](evaluate.py)
- 呼吸层对比：[breathing_eval.py](breathing_eval.py)
- 测试：[test_strategy.py](test_strategy.py)

## 跑起来

```bash
cd calendar-rescheduler/backend
python core.py                 # 单场景（含呼吸块）
python persona_cases.py        # 同一冲突 × 五种画像
python evaluate.py             # 12 画像 × 80 中断
python breathing_eval.py       # 有/无呼吸层对比
python -m unittest -v test_strategy
```

## 最小调用

```python
from rescheduler.core import (from_state, Planner, Disruption,
                              ensure_breathing)
from protocol import sanitize_changes

events, persona = from_state(state, base_date)
events = ensure_breathing(events, persona)       # 午饭/下午茶/收尾/长块后恢复
proposal = Planner(persona, {"stable": False, "recovery": False}).propose(
    events, Disruption("insert", 0, start=14 * 60, dur=60, title="临时会议")
)
changes = sanitize_changes(proposal.changes)
```

`proposal.plan` 保留完整方案，`proposal.bill` 是影响账单，`proposal.alternatives` 是 A/B/C。

## 指标（960 次提案）

| 策略 | p95 | 改动/次 | 目标 | 社交风险 | 期望接受值 | 保留冲突 | 协议兼容 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 通用 | 1.7ms | 1.34 | 0.97 | 0.34 | -0.129 | 3% | 100% |
| 画像 | 1.5ms | 1.20 | 0.83 | 0.25 | -0.018 | 15% | 100% |

画像策略更保守：少动、少欠社交、期望接受值更高，但会更多地建议"先保留冲突"。绝对值为负，是因为账单把"排得太满 / 留白不足 / 休息被挤掉"都算进了代价。

## 呼吸层

`ensure_breathing` 会按画像写入午饭、下午茶、收尾和长专注后的恢复块；只有"当日自由时间 − 休息 ≥ 留白底线"时才加。重排打分同时惩罚低留白、超容量和休息损失。对比结果见 `out_breathing.json`：每天约 54 分钟可见休息，留白从 324 降到 242 分钟（仍高于底线），目标略升。
