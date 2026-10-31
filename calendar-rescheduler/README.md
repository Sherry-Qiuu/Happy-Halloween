# 日历重排 · 前后端设计

这是「行间」的日历重排实验包：前端演示拖拽 / 缩放 / AI 对话协商，后端提供画像感知的重排策略、呼吸层和留白预算。独立于主应用，方便先评审交互与策略，再接入 `xingjian-agent`。

## 目录

| 路径 | 说明 |
| --- | --- |
| [`backend/`](backend/) | Python 重排内核：约束、候选、画像打分、呼吸层、留白/容量、基准与测试 |
| [`frontend/`](frontend/) | 纯前端交互 Demo：拖拽移动、上下边缘改时长、冲突后在 AI 对话里出方案 |
| [`backend/STRATEGY.md`](backend/STRATEGY.md) | 策略说明：六步算法、P1–P8 参数、A18–A20 动作、基准 |
| [`frontend/README.md`](frontend/README.md) | 交互说明、运行方式、接入步骤 |

## 前端

直接打开 [`frontend/index.html`](frontend/index.html)，或：

```bash
cd frontend
python -m http.server 8000
# http://127.0.0.1:8000
```

- 拖动时间块移动，拖动上下边缘改时长，15 分钟吸附。
- 产生冲突后事件变红，右侧 AI 自动收到并给出方案。
- 对话框里可继续说「别动客户会」「把专注块缩短到 45 分钟」「换到明天下午」。
- 每个方案有账单、逐条改动、2–3 个备选；应用 / 保留 / 撤销。
- P1–P8 画像切换；同一冲突会给出不同决定。

## 后端

```bash
cd backend
python core.py                 # 单场景（含呼吸块）
python evaluate.py             # 12 画像 × 80 中断
python breathing_eval.py       # 有/无呼吸层对比
python -m unittest -v test_strategy
```

核心调用：

```python
from rescheduler.core import ensure_breathing, Planner, Disruption

events = ensure_breathing(events, persona)       # 午饭/下午茶/收尾/长块后恢复
proposal = Planner(persona).propose(events, disruption)
```

- `ensure_breathing`：按画像写入休息块，且不突破留白底线。
- `daily_ratio / slack_reserve`：日容量 62–82%、留白底线 45–90 分钟。
- `_capacity_ok`：过滤会把某天排爆的移动。
- `bill` 输出 `slack_minutes / breathing_minutes / load_pct / breathing_loss`。

## 接入 `xingjian-agent`

1. 后端：把 `backend/core.py` 作为重排模块；`proposal.changes` 已兼容 `protocol.py` 的 `move_event / add_event / remove_event`。
2. 前端：把 `frontend/planner.js` 作为客户端策略，或直接调用后端 `/replan`；把 `frontend/app.js` 的拖拽与对话渲染移植到 `web/agent.js`。
3. 画像：用 `sched_profile.scheduling_params()` 覆盖 `PersonaPolicy` 默认值。

## 说明

- 这是独立概念演示，不是官方产品；不包含 Reclaim 的商标或资产。
- 前端使用演示数据，不连接真实日历；刷新会重置。
- 后端基准是合成场景，数值用于比较策略，不作为真实用户承诺。

---

仓库名 `Happy-Halloween` 只是代号，主项目仍是「行间」。
