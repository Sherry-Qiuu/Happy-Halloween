# AI 日历重排策略 v0.1

> 目标：在一次变化发生后 **1 毫秒级**给出一个可解释的重排提案；提案满足"关键产品动作"，并按用户画像调整取舍。
> 代码：`rescheduler/core.py` · 基准：`rescheduler/evaluate.py` · 测试：`rescheduler/test_strategy.py`

---

## 0 一句话

**不做全局最优，做"局部最小代价 + 画像权重 + 可解释提案"。**

全局重排会让用户觉得生活被重写；策略只在冲突附近搜索，把"动几件、丢多少目标、欠几笔通知、要不要问"算清楚，然后给一个主方案和两三个备选。

---

## 1 目标与非目标

**目标**

1. 变化发生后，在 **10ms 内**返回提案（实测 p95 < 1ms）。
2. 硬约束永不违反：H0 / 锁定 / 时间边界 / 截止。
3. 改动尽量少，目标尽量保，社交尽量不欠。
4. 同一场景下，不同画像给出不同建议（P1 保目标，P2 保稳定，P7 用叠加）。
5. 输出可直接被 `xingjian-agent` 的 `sanitize_changes` 接受（最多 4 条）。

**非目标**

- 不做整月全局最优；不做多目标长期规划；不接真实多人日历。
- 不用 LLM 做约束求解。LLM 只负责把结构化提案写成人话。
- 不自动替用户发送通知；不自动改截止日期。

---

## 2 输入 / 输出

### 输入

```json
{
  "base_date": "2026-11-02",
  "persona_type": "P2",
  "scheduling_params": {"block_minutes": 25, "buffer_minutes": 5, "avoid_after": "21:00"},
  "state": {"stable": false, "recovery": false, "prompts_today": 0},
  "events": [
    {"title": "英语面试 · 自我介绍", "date": "2026-11-02", "start": 14.0, "dur": 30,
     "hardness": 1, "kind": "meeting", "notify": true},
    {"title": "整理作品集素材", "date": "2026-11-02", "start": 11.0, "dur": 45,
     "hardness": 3, "kind": "focus"}
  ],
  "disruption": {"kind": "insert", "day": 0, "start": 840, "dur": 60, "title": "临时会议"}
}
```

- `hardness`：H0–H5；H0 或 `locked=true` 永不移动。
- `start`：分钟或小时；适配器会统一成分钟。
- `persona_type`：P1–P8；`scheduling_params` 可直接来自 `xingjian-agent/sched_profile.py` 的 `scheduling_params(profile)`。

### 输出

```json
{
  "summary": "动 2 件，目标不变，需通知",
  "changes": [
    {"type": "add_event", "date": "2026-11-02", "title": "临时会议", "start": "14:00", "duration": 60},
    {"type": "move_event", "date": "2026-11-02", "title": "英语面试 · 自我介绍", "start": "15:05", "duration": 30}
  ],
  "tier": "preview",
  "bill": {"changes": 2, "goal_pct": 100, "conflict": 0, "notifications": 1,
           "social": 1.0, "slack_minutes": 535, "breathing_minutes": 50,
           "load_pct": 25, "breathing_loss": 0},
  "explanation": ["「英语面试 · 自我介绍」从 14:00 挪到 15:05，避开冲突"],
  "alternatives": [{"kind": "keep", "summary": "保留现有安排，冲突先留着"}],
  "warnings": ["涉及他人，改动前需要通知。"],
  "infeasible": false,
  "latency_ms": 0.47
}
```

`changes` 与 `protocol.py` 的 `move_event / add_event / remove_event` 完全兼容；超过 4 条自动截断并标记 `deferred`。

---

## 3 算法：六步，搜索空间很小

```
呼吸层 → 变化 → 冻结硬约束 → 找冲突集 → 生成候选方案 → 画像打分 → 选主方案 + 备选 → 分级确认
```

最坏情况：7 天 × 96 个 15 分钟槽 × 3 个受害者 ≈ 2,000 次比较；不做全局搜索，不调用 LLM。

### 3.0 呼吸层（先于一切排布）

`ensure_breathing(events, persona)` 在进入重排前，先把"休息"写进日历：

- 固定锚点：午饭 12:00、下午 15:30、收尾 17:30，按画像取 10–45 分钟。
- 长专注之后：连续深度块 ≥ `break_after_deep`（60–120 分钟）时，在后面补 10–20 分钟恢复。
- 每天最多 4 个；**只有"当日自由时间 − 休息 ≥ 留白底线"时才加**，不拿临时计划的空间换休息。
- 休息是普通事件（`kind="break"`），参与冲突检测；但打分时会惩罚"搬走/缩短休息"。

这样日历上真的有空档，而不是只在算法里假设用户会休息。

### 3.1 冻结

- `locked`、`hardness == 0` 的事件进入不可动集合。
- 按画像的 `avoid_after`、工作时间、缓冲，算出每天可用的空档。
- 稳定模式 / 恢复模式在这里改变策略：稳定模式只允许低代价单件改动；恢复模式禁止跨天移动。

### 3.2 找冲突集

只找与"新事件 / 被延长事件 / 生病日"直接重叠的事件，不做全周扫描。

- 插入：新事件 vs 所有事件。
- 切换：把新任务当作一个可移动的专注块，先找位置，再处理冲突。
- 会议延长：延长后的时间段 vs 所有事件。
- 生病：当天 `focus/task` 事件。
- 取消：直接返回"取消，不自动填满"。

### 3.3 生成候选（每个受害者 3–5 个）

| 候选 | 适用 | 代价 |
| --- | --- | --- |
| `keep` | 所有 | 冲突保留，目标 0 |
| `move` | H1–H5 且有空档 | 位置变化、跨天、社交风险 |
| `shorten` | 可拆分且有空档 | 目标按比例损失，产生待补 |
| `defer` | 次日同一时间可用 | 跨天、可能碰截止 |
| `stack` | 仅 P7 / 手动开启 | 40% 折算，需确认 |
| `skip` | H5 / 恢复模式 | 目标归零，进待补 |

- 本地空档优先；如果本地没有，**仍然给一个"远距离空档"**，附带 +3.0 惩罚，让画像决定"值不值得跳"。
- 每个受害者最多 3 个候选；组合数上限 24，避免组合爆炸。

### 3.4 画像打分

每个候选先算：目标保留、改动条数、社交风险、能量错配、连续性损失、待补分钟，以及**留白 / 日容量 / 呼吸损失**。

```
utility = goal_w × goal
        − change_w × (changes / 4)
        − social_w × social
        − energy_w × energy_mismatch
        − continuity_w × continuity
        − debt_w × (debt / 2)
        − prompt_w × prompt_cost
        − slack_w × slack_penalty
        − capacity_w × capacity_penalty
        − breathing_w × breathing_loss
        + breathing_w × 0.8 × breathing_reward

slack_penalty    = max(0, 留白底线 − 当日留白) / 留白底线
capacity_penalty = max(0, 当日负载 − 日容量) / (1 − 日容量)
breathing_loss   = 被搬走/缩短的休息分钟
breathing_reward = min(1, 保留的休息分钟 / (2 × 单次休息))

p_accept = sigmoid(1.0 + accept_bias
                   − 0.6 × changes − 1.0 × social − 0.5 × energy
                   − 0.8 × conflict + 1.2 × goal
                   − 0.35 × capacity_penalty
                   − 0.25 × slack_penalty
                   − 0.20 × breathing_loss
                   + 0.20 × breathing_reward)
```

选 `expected` 最高的方案。**不是选目标最高的方案**：如果用户画像把社交承诺、留白或休息看得更重，系统会建议"先保留冲突"或"少塞一块"，同时把其他方案放进备选。

### 3.5 分级确认

| 条件 | 层级 |
| --- | --- |
| 涉及他人 / 跨天 / H0–H1 | `preview` 预览 |
| 改动 ≥2 件或有待补 | `toast` 提示 + 撤销 |
| 单件、当天、可逆 | `silent` 静默 + 撤销 |
| 稳定模式 / 当天已打断 ≥2 次 | `digest` 摘要 |

---

## 4 画像映射

默认权重（可在 `personas.py` / `PersonaPolicy` 里覆盖）：

| 类型 | 目标 | 改动 | 连续 | 社交 | 能量 | 待补 | 默认行为 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **P1 期限冲刺** | 1.10 | 0.20 | 0.30 | 0.40 | 0.30 | 0.35 | 目标优先，接受远移 |
| **P2 稳定节奏** | 0.55 | 1.00 | 1.00 | 1.10 | 0.50 | 0.70 | 保固定时段，宁可留冲突 |
| **P3 乐观估时** | 0.80 | 0.50 | 0.40 | 0.50 | 0.65 | 0.80 | 多留缓冲，少塞 |
| **P4 保守留白** | 0.65 | 0.40 | 0.60 | 0.50 | 0.45 | 0.50 | 不排满，先加一块 |
| **P5 碎片救火** | 0.75 | 0.40 | 0.30 | 0.50 | 0.75 | 0.60 | ≤30 分钟，会议前 |
| **P6 目标囤积** | 0.80 | 0.50 | 0.50 | 0.70 | 0.50 | 0.60 | 只推进一个目标 |
| **P7 高启动摩擦** | 0.70 | 0.55 | 0.60 | 0.40 | 0.50 | 0.70 | 15 分钟启动，允许叠加 |
| **P8 低记录** | 0.60 | 0.45 | 0.50 | 0.50 | 0.40 | 0.50 | 不猜，给一问一答 |

`sched_profile.scheduling_params()` 的 `block_minutes / buffer_minutes / avoid_after / estimate_bias` 会覆盖块长、缓冲、夜间边界和每日放量。**画像只改参数，不改变"是否主动"**（遵守 R8）。

### 4.0 呼吸与留白参数

| 类型 | 日容量 | 留白底线 | 休息 | 呼吸锚点 | 长块后休息 |
| --- | --- | --- | --- | --- | --- |
| **P1 期限冲刺** | 82% | 45 分钟 | 10 分钟 | 12:00 / 15:30 / 17:30 | 120 分钟后 |
| **P2 稳定节奏** | 70% | 75 分钟 | 15 分钟 | 12:00 / 15:30 / 17:30 | 90 分钟后 |
| **P3 乐观估时** | 72% | 75 分钟 | 15 分钟 | 12:00 / 15:30 / 17:30 | 90 分钟后 |
| **P4 保守留白** | 62% | 90 分钟 | 20 分钟 | 12:00 / 15:30 / 17:30 | 90 分钟后 |
| **P5 碎片救火** | 68% | 60 分钟 | 10 分钟 | 10:30 / 12:30 / 15:30 | 60 分钟后 |
| **P6 目标囤积** | 65% | 75 分钟 | 15 分钟 | 12:00 / 15:30 / 17:30 | 90 分钟后 |
| **P7 高启动摩擦** | 70% | 60 分钟 | 10 分钟 | 10:30 / 15:30 / 17:30 | 60 分钟后 |
| **P8 低记录** | 65% | 75 分钟 | 15 分钟 | 12:00 / 15:30 / 17:30 | 90 分钟后 |

**日容量**：非休息事件占工作窗口的比例。**留白底线**：一天里必须剩下的、可被临时计划使用的分钟数。

### 4.1 同一个冲突，五种决定

测试场景：客户会 14:00–15:00（H1），专注块 15:00–16:30（H3），插入 14:00–15:00 的临时会。

| 画像 | 选择 | 原因 |
| --- | --- | --- |
| P1 | 移动客户会（另写入临时会） | 目标优先，接受社交风险 |
| P2 | **保留冲突** | 社交承诺 + 连续性权重最高；移动会议比留冲突更伤 |
| P5 | 移动客户会（另写入临时会） | 碎片型，先清冲突 |
| P7 | 缩短客户会 | 不愿跨天，也不愿整块移动 |
| P8 | 缩短客户会 | 数据少，保守，不猜 |

这不是"随机差异"，是同一套打分在不同权重下的可解释结果。UI 会同时展示备选，P2 也可以手动选"移动会议"。

---

## 5 关键产品动作如何被满足

| 动作 | 在策略里的落点 |
| --- | --- |
| A01 硬度五档 | `Event.hardness` + `locked`；H0 永不移动 |
| A02 影响账单 | `Proposal.bill`：改动数、目标保留、社交、通知、待补、留白、呼吸、负载 |
| A03 半径选择 | `PersonaPolicy.radius_days`；远移有显式惩罚 |
| A04 A/B/C | `Proposal.alternatives`：keep / shorten / move |
| A05 进行中 | `Disruption` 支持 insert / switch / overrun / cancel / illness / external |
| A06 叠加挂载 | `Plan.stacked`，只对 P7 默认开启，手动确认 |
| A07 一键撤销 | 提案是事务；应用后由前端撤销（策略不写状态） |
| A08 无解菜单 | `keep` + `conflict_left`；只有连备选都没有时 `infeasible=true` |
| A09 通知债 | `bill["notifications"]` + `social`；通知草稿由上层生成 |
| A10 稳定模式 | `state.stable` → 只允许单件低代价改动，否则 `digest` |
| A12 打扰账本 | `tier` 四档；`prompts_today >= 2` 自动降为摘要 |
| A15 恢复模式 | `state.recovery` → 禁止跨天，优先 shorten / skip |
| A16 锁死审计 | 冲突事件若 `locked`，只返回 `keep` + 备选；上层显示"哪把锁" |
| A18 呼吸层 | `ensure_breathing()` 写入 `kind="break"`；午饭/下午/收尾 + 长块后恢复 |
| A19 日容量与留白 | `daily_ratio` + `slack_reserve`；`_capacity_ok()` 过滤会排爆的移动 |
| A20 重排留白检查 | `_score` 惩罚低留白、超容量、休息损失；`bill` 输出 `slack/load/breathing` |

---

## 6 基准结果

`python evaluate.py`：12 类画像 × 80 次中断，共 960 次提案/策略。

| 策略 | p50 | p95 | 平均改动 | 目标 | 社交风险 | 接受率 | 期望值 | 保留冲突 | 无解 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 通用权重 | 0.6ms | 1.7ms | 1.34 | 0.97 | 0.34 | 0.701 | -0.129 | 3% | 3% |
| **画像权重** | **0.5ms** | **1.5ms** | **1.20** | 0.83 | **0.25** | 0.676 | **-0.018** | 15% | 4% |

**怎么读：**

- 延迟不是问题：p95 **≤ 1.7ms**（含留白/呼吸计算），端到端主要花在网络和渲染。
- 画像策略的**期望接受价值相对提升 +0.111**，12/12 类画像都不低于通用策略；代价是目标覆盖从 0.97 降到 0.83、保留冲突从 3% 升到 15%。
- 绝对值为负，是因为打分把"排得太满 / 留白不足 / 休息被挤掉"都算进了代价；这正是这版新增的呼吸约束。
- 这不是"更会排"，而是"更会选择不排"：对 P2 这类用户，移动一个客户会的伤害大于留一个冲突，系统就建议保留，并把移动放进备选。
- 如果产品目标更偏"清空冲突"，把 `social_w` 调低；如果更偏"排满"，把 `slack_w / capacity_w` 调低。权重是产品决策，不是算法常数。

### 6.1 分画像

| 画像 | 通用 → 画像 | 接受率 |
| --- | --- | --- |
| 大一新生 | -0.205 → -0.159 | 0.66 |
| 考研党 | 0.397 → 0.420 | 0.72 |
| 社团负责人 | -0.001 → 0.027 | 0.69 |
| 实习+上课 | 0.018 → 0.056 | 0.70 |
| 高启动摩擦 | -0.175 → -0.103 | 0.67 |
| 完美主义 | 0.019 → 0.065 | 0.70 |
| 跨时区 | -0.730 → -0.287 | 0.62 |
| 照护者 | -0.352 → -0.271 | 0.65 |
| 运动员 | -0.730 → -0.287 | 0.62 |
| 自由职业 | 0.018 → 0.056 | 0.70 |
| 低记录 | -0.205 → -0.159 | 0.66 |
| 考试周 | 0.397 → 0.420 | 0.72 |

跨时区/运动员的绝对值仍低，是因为基准里给 P2 安排了大量社交冲突；策略选择保留冲突是"符合画像"的，不代表场景理想。

### 6.2 呼吸层带来的变化

`python breathing_eval.py`：同一批 12 类画像 × 60 次中断，只比较"加呼吸层 / 不加呼吸层"。

| 版本 | 目标 | 改动 | 社交 | 冲突 | 留白/日 | 呼吸/日 | 负载 | 休息损失 | 接受率 | 延迟 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 无呼吸层 | 0.82 | 1.18 | 0.24 | 0.17 | 324 分钟 | 0 | 58% | 0 | 0.68 | 0.6ms |
| **有呼吸层** | **0.83** | 1.46 | 0.23 | 0.18 | 242 分钟 | **54 分钟** | 61% | 0.2 分钟 | **0.68** | 1.1ms |

**读法**：每天多出约 **54 分钟可见休息**，留白从 324 降到 242（仍远高于 60–90 分钟的底线），目标还略升；代价是改动 +0.28、延迟 +0.5ms。休息不是免费的，它确实占时间；但"留白底线"保证它不会把临时计划的空间吃光。

---

## 7 接入 `xingjian-agent`

### 7.1 最小接入

```python
from rescheduler.core import (from_state, Planner, Disruption,
                              ensure_breathing)
from protocol import sanitize_changes

events, persona = from_state(state, base_date)
events = ensure_breathing(events, persona)       # 先写入午休/下午茶/收尾/长块后恢复
proposal = Planner(persona, {"stable": stable, "recovery": recovery,
                             "prompts_today": prompts_today},
                   base_date).propose(events, disruption)

changes = sanitize_changes(proposal.changes)   # 再走一次协议校验
return {"summary": proposal.summary, "changes": changes, ...}
```

`from_state` 读取 `state["schedule"]`（`title/date/start/dur`），并可选读取：
- `state["persona_type"]`：P1–P8；
- `state["scheduling_params"]`：`sched_profile.scheduling_params()` 的输出；
- 每个 event 的 `hardness / locked / kind / notify`。

### 7.2 建议的调用点

1. **插入/延长/取消**：先把变化写进事件列表，调用 `propose`。
2. **AI 面板**：把 `proposal.as_dict()` 作为结构化上下文交给 LLM，让 LLM 只负责措辞，不重新做约束。
3. **应用**：前端按 `changes` 逐条应用；`proposal.plan` 保留用于撤销。
4. **通知**：`bill["notifications"]` 决定是否生成通知草稿；策略不发送。

### 7.3 为什么不让 LLM 直接排

- 约束求解需要确定性：锁定、H0、截止、时区不能靠采样。
- 延迟：LLM 一次 1–3 秒，策略 <1ms；LLM 适合解释，不适合搜索。
- 可审计：每个决定都能回到权重和约束，方便复盘与测试。

---

## 8 失败模式与边界

| 情况 | 策略行为 |
| --- | --- |
| 全部锁定 | `keep` + `infeasible=true` + 备选为空；上层显示"哪把锁" |
| 本地无空位 | 先给 shorten/defer；再给远移（+3.0 惩罚） |
| 目标不可拆 | 不生成 shorten/split；只 move/defer/keep |
| 截止在前 | 不 defer 过截止；优先 shorten 或 keep |
| 稳定模式 | 社交/多件改动 → `digest`，保留冲突 |
| 恢复模式 | 禁止跨天；优先 shorten/skip/keep |
| 叠加 | 只对 P7 默认开启；只挂到低负荷课堂/会议；40% 折算；不写协议 |
| 超过 4 条改动 | 截断到 4 条，`deferred` 标记；下一批处理 |
| 找不到事件 | 返回空提案 + 说明，不猜 |
| 休息被挤掉 | `breathing_loss > 0` 写入账单和警告；优先保留休息，其次才动它 |
| 当天会排爆 | `_capacity_ok()` 过滤候选；实在无解时在账单里标 `load_pct` 和低留白警告 |

**已知不足**

- 没有真正的多人日历 / free-busy；`social` 只是风险代理。
- 没有学习闭环：权重是静态画像，未根据接受/拒绝更新。
- 没有跨周优化；半径最多 7 天。
- `stack` 产出是固定 40%，未按课程校准。
- 基准是合成场景，不是真实用户；数值用于比较策略，不用于承诺效果。

---

## 9 下一步

1. 把 `sched_profile.scheduling_params()` 接进 `from_state`，用真实画像覆盖默认权重。
2. 在 `server.py` 加一个 `POST /api/agent/replan`，返回 `proposal.as_dict()`；前端把 `changes` 走现有 `applyProposal`。
3. 用真实 7 天日程回放，校准 `social_w / continuity_w / accept_bias`，以及 `daily_ratio / slack_reserve / break_minutes` 三组呼吸参数。
4. 把 `tier` 接到 A12 的确认预算：`preview` 必须用户点，`silent` 只出 Toast + 撤销。
5. 用 `sim/calendar_stress.py` 的 28 天中断流跑策略，比较 P2/P4 的长期代价。

---

## 10 复现

```bash
cd calendar-rescheduler/backend
python core.py                 # 单场景 demo（含呼吸块）
python persona_cases.py        # 同一冲突 × 五种画像
python evaluate.py             # 12 画像 × 80 中断基准
python breathing_eval.py       # 有/无呼吸层对比
python -m unittest -v test_strategy
```

输出：`out_benchmark.json`（p50/p95、改动、目标、接受率、期望值、协议兼容率）、`out_breathing.json`（留白、呼吸、负载、休息损失）。
