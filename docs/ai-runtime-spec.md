# AI 运行规范

> v1.0 · 2026-11-01 · 适用 [`xingjian-agent/`](../xingjian-agent) 的 AI 面板（流式对话 + 反思报告）
>
> 一句话：**模型负责起草，服务端负责裁决，用户负责确认。** 规则由代码兜底，延迟和成本各有预算，改任何一环都要过评测门槛。

## 0 这份规范管什么

| 管 | 不管 |
| --- | --- |
| 模型调用链路、提示词与上下文预算 | 界面样式与组件（见 [`warmgrid-skin.css`](../xingjian-agent/web/warmgrid-skin.css)） |
| 提案协议与白名单 | 重排算法与打分（见 [`STRATEGY.md`](../calendar-rescheduler/backend/STRATEGY.md)） |
| 画像写入、输出策略、降级与观测 | 账号、会话、邮箱（见 [`xingjian-agent/README.md`](../xingjian-agent/README.md)） |
| 改动流程与评测门槛 | 部署与反代（见 `xingjian-agent/deploy/`） |

读者：要改 `prompts.py`、`protocol.py`、`policy.py`、`sched_profile.py`、`deepseek.py`、`server.py` 或 `web/agent.js` 的人。

## 1 角色分工

| 角色 | 能做什么 | 绝对不能做什么 |
| --- | --- | --- |
| 模型（DeepSeek） | 用自然语言说明影响、起草改动、按格式提交画像增量 | 直接写用户数据；绕过格式；替用户确认 |
| 服务端 | 校验、裁剪、裁决语气、决定是否写画像 | 编造模型没说的话；把违规句子发出去 |
| 用户 | 点「采用」或「保留现状」 | —— |
| 前端 | 渲染、发请求、按账号存聊天记录 | 自行推断提案是否生效 |

模型看不到 `<persona>` 原文之外的任何画像标签，用户也看不到。**画像的写入权在服务端**，模型的增量只是「申请」。

## 2 一次请求的生命周期

```text
浏览器 POST /api/agent/chat
 ① 同源校验 → 会话 → 每用户限流（40 次/分钟）
 ② 服务端取画像，生成 <persona>（≤200 字，只给参数）
 ③ 拼装消息：系统提示（2087 字）+ persona + <state>（≤6000 字）+ 历史
 ④ 调用上游，读流；正文与提案块在 ProposalSplitter 里分离
 ⑤ 提案块闭合后 parse_turn：proposal / persona / draft 三分
 ⑥ protocol.sanitize_changes：白名单、字段、值域、去重、截到 4 条
 ⑦ sched_profile.advance：校验 quote 是用户原话子串，通过才写；本轮写入下一轮生效
 ⑧ policy.apply：危机 → 事实 → 语气；正文压到 3 句
 ⑨ 按 4 字 / 12 ms 回放为 delta 事件，再发 proposal，最后 done
```

关键点：**上游的 token 不直通浏览器。** 服务端把整段收完、过完策略，才分片回放。所以第 ⑨ 步的第一个 `delta` 一定已经合法。

## 3 硬约束

改代码不能破这九条；破了就是协议级回归，不是文案问题。

| # | 约束 | 值 | 位置 |
| --- | --- | --- | --- |
| 1 | 单轮改动条数 | ≤ 4 | `protocol.MAX_CHANGES` |
| 2 | 动作白名单 | 6 种：`move_event` `add_event` `remove_event` `add_task` `add_next_action` `log_actual` | `protocol.CHANGE_TYPES` |
| 3 | 字段与值域 | 日期 `YYYY-MM-DD`、时间 `HH:MM`、时长 5–240 分钟、标题 ≤60 字 | `protocol.normalize_*` |
| 4 | 正文句数 | ≤ 3 句（反思报告不限） | `policy.MAX_SENTENCES` |
| 5 | 画像写入 | 单轮 ≤ 3 条；`quote` 必须是用户原话子串、≤ 30 字；挡元指令 | `sched_profile.validate_delta` |
| 6 | 危机短路 | 命中自伤表述 → 整段替换固定文案，不再过其它规则 | `policy.CRISIS_REPLY` |
| 7 | 服务端专用事件 | `persona` / `draft` 永不转发浏览器 | `server._handle_chat` |
| 8 | 未闭合提案块 | 直接丢弃，不让 JSON 漏进气泡 | `ProposalSplitter.finish` |
| 9 | SSE 事件名 | `delta` / `proposal` / `error` / `done` 四个，顺序固定 | `server._write_event` |

事件顺序只有两种合法形态：

```text
正常：delta* → [proposal] → done
失败：error → done
```

`error` 之后不会再有 `delta`；`done` 永远是最后一帧（浏览器据此收尾）。

## 4 预算

| 预算项 | 当前值 | 位置 | 超了会怎样 |
| --- | --- | --- | --- |
| 系统提示词 | 2087 字 | `prompts.CHAT_PROMPT` | 每轮固定成本，改长等于每轮多付费 |
| 画像块 | ≤ 200 字 | `sched_profile.MAX_PROMPT_CHARS` | 超限被截断，参数丢一半 |
| 状态快照 | ≤ 6000 字 | `prompts.MAX_STATE_CHARS` | 截断并标「已截断」，模型看到残缺状态 |
| 历史条数 | 前端带 10 条；服务端上限 20 条 | `agent.js` / `server.MAX_MESSAGES` | 请求被 400 拒绝 |
| 单条消息 | ≤ 2000 字 | `server.MAX_CONTENT` | 400 |
| 请求体 | ≤ 64 KB | `server.MAX_BODY` | 400 |
| 输出 tokens | 1200 | `AGENT_MAX_TOKENS` | 回复被上游截断，可能丢掉提案块 |
| 上游超时 | 60 s | `AGENT_TIMEOUT` | `error`：上游超时 |
| 调用频率 | 40 次/分钟/用户 | `AGENT_RATE_LIMIT` | 429 |
| 回放节奏 | 4 字 / 12 ms | `policy.CHUNK_CHARS` + `server.REPLAY_DELAY` | 长文变慢，见 §5 |

`AGENT_MAX_HISTORY`（默认 8）和 `REPORT_MAX_TOKENS`（700）目前**读入但未生效**：历史条数实际由前端 10 条与 `MAX_MESSAGES=20` 决定，报告走 `AGENT_MAX_TOKENS`。这是已知缺口，不是可调开关。

## 5 延迟结构

一次对话的墙钟时间拆成两段：

```text
首字延迟 ≈ T(上游生成整段) + ε
总时长   ≈ T(上游生成整段) + T(回放)
T(回放) = ceil(正文字数 / 4) × 12 ms
```

- 3 句中文约 26 字 → 回放约 0.08 s（`scripts/ai_eval.py` 实测）。
- 100 字的长回复 → 回放约 0.3 s。

**为什么不做 token 直通**：边流边改会先让用户看到「你又超时了」，再被替换成合规句子——那比慢更糟。所以正文先缓冲、过策略、再回放；这是有意的延迟换正确性。

首字延迟的 95% 来自上游，离线无法测。线上目标：**首字 < 3 s**；本地部分（分流 + 策略 + 解析）p95 ≤ 8 ms，由评测脚本守住。

## 6 失败与降级

| 场景 | 服务端行为 | 用户看到 |
| --- | --- | --- |
| 未配 `DEEPSEEK_API_KEY` | 标记未配置，`/api/agent/chat` 走离线示例 | 顶栏「AI 离线」，回复带示例提案 |
| `AGENT_MOCK=1` | `mock_agent` 逐字吐出，按关键词决定是否给提案 | 同离线，用于演示 |
| 上游 4xx / 5xx | 翻成中文 `error` 事件 | 一句错误说明，可重试 |
| 上游超时 60 s | 同上 | 「上游超时」 |
| 上游流中断 | 已收正文仍按策略回放 | 正常回复，或 `error` |
| 提案块未闭合 / JSON 坏 | 丢弃提案，正文照发 | 只有文字，没有卡片 |
| 提案字段不合规 | 单条丢弃；全丢则无卡片 | 卡片条数变少 |
| 会话过期 | 401 | 登录页 |
| 超过限流 | 429 | 「请求太频繁」 |
| 截图式追问（客户端断连） | `BrokenPipe` 直接 return，不写 `done` | —— |

**没有重试**：上游失败一次就返回错误。多试一次的代价是用户等待翻倍，不如让他自己决定。这条规则如需改动，必须同时改 §9 的门槛。

## 7 观测

| 位置 | 内容 | 用途 |
| --- | --- | --- |
| 服务端日志 | `[policy] {"rule": ...}` 每条违规一行 | 判断语气规则是否频繁误伤 |
| `audit.log` | 注册、登录、改密、删号（JSONL） | 账号侧审计，与 AI 无关但同一目录 |
| 前端 | AI 状态点（离线 / 在线）、方案卡 | 用户可见的健康信号 |
| 评测脚本 | 协议、策略、画像、预算四组用例 | 离线回归，见 §9 |

建议采集的六项指标（当前口径，尚未接入在线看板）：

1. **提案合法率**：上游提案通过 `sanitize_changes` 的比例——低说明提示词或模型在漂。
2. **采用率**：`data-agent-apply` 点击数 / 卡片出现数。
3. **平均改动数**：每张卡片几条改动，目标接近 1–2。
4. **策略命中率**：违规条目数 / 回复数，应逐周下降。
5. **首字延迟**：从 POST 到第一个 `delta`。
6. **画像写入率**：每轮 accepted / rejected 条数，rejected 突增说明模型在编造原话。

前四项里除采用率外都能离线测；采用率需要前端埋点，目前没有。

## 8 改动流程

改 AI 相关代码，按表执行；没有跑完不许提交。

| 改什么 | 必跑 | 额外检查 |
| --- | --- | --- |
| `prompts.py` 文案 / 结构 | 单元测试 + `ai_eval` | 提示词长度变化记进 §4 |
| `protocol.py` 白名单 / 字段 | 单元测试 + `ai_eval` + `dom_smoke` | 前端 `CHANGE_TAGS` 要同步 |
| `policy.py` 规则 | 单元测试 + `ai_eval` | 新规则要能说清「为什么不能靠提示词」 |
| `sched_profile.py` 槽位 / 阈值 | 单元测试 + `ai_eval` | 更新 `docs/profile-rules.md` 条款号 |
| `deepseek.py` 解析 / 分流 | 单元测试 + `ai_eval` | 未闭合块必须仍然丢弃 |
| `web/agent.js` 卡片渲染 | `dom_smoke` | 字段名以 `protocol.py` 为准 |
| 预算常量 | `ai_eval` + 一次真实对话 | 记下新的 p95 与首字延迟 |

必须跑的四条命令：

```bash
cd xingjian-agent
python -m unittest discover -s tests   # 108 项，含画像与策略
python scripts/ai_eval.py              # 44 项运行期用例 + 预算
node tests/dom_smoke.mjs               # 22 项前端链路
node tests/auth_smoke.mjs              # 17 项登录链路
```

**冻结项**（改名等于改协议，前后端必须同批改并同步升版本）：

- `<<<PROPOSAL>>>` / `<<<END>>>` 两个标记：`prompts.PROPOSAL_START` / `PROPOSAL_END`
- 6 个 `change.type` 字符串
- 4 个 SSE 事件名
- `<persona>` 与 `<state>` 两个包裹标签

## 9 评测门槛

`scripts/ai_eval.py` 是这套规范的执行者，退出码就是门槛：

| 指标 | 门限 | 当前 |
| --- | --- | --- |
| 协议用例 | 100% | 15/15 |
| 策略用例 | 100% | 13/13 |
| 画像用例 | 100% | 9/9 |
| 本地开销 p95 | ≤ 8 ms | 约 0.1 ms |
| 回放时长 | ≤ 2 s | 0.084 s |

```bash
python scripts/ai_eval.py                 # 人读报告，非零退出即失败
python scripts/ai_eval.py --json out.json # 附带机器可读结果
```

门限写在脚本顶部（`GATE_*`），要放宽必须在本文件 §9 里先写理由。建议接进 CI：`ai_eval` 不联网、不依赖 key，可以每条 PR 都跑。

## 10 已知缺口

| 缺口 | 影响 | 什么时候该补 |
| --- | --- | --- |
| 上游失败不重试 | 一次抖动就是一次失败 | 有真实用户量后，按错误码区分可重试 |
| 无 token 计量 | 成本只能靠估算 | 接入用量统计 |
| 首字延迟无在线采集 | 体验问题只能靠感觉 | 前端加一个 `performance.mark` |
| `AGENT_MAX_HISTORY` / `REPORT_MAX_TOKENS` 未生效 | 配置项误导 | 要么接上，要么删掉 |
| 限流在进程内存 | 多实例失效 | 换 Redis 或反代层限流 |
| 回放延迟固定 12 ms | 长文偏慢 | 可按剩余字数动态收敛 |
| 画像对用户不可见 | 排查「AI 怎么知道这个」成本高 | 设置页加只读视图 |

## 11 与其它文档的关系

| 文档 | 定位 |
| --- | --- |
| 本文 | 运行期契约：约束、预算、降级、门槛 |
| [`xingjian-agent/docs/profile-rules.md`](../xingjian-agent/docs/profile-rules.md) | 画像与输出策略的条款原文（P-0…P-10、7.x） |
| [`calendar-rescheduler/backend/STRATEGY.md`](../calendar-rescheduler/backend/STRATEGY.md) | 重排算法、P1–P8 参数、失败模式 |
| [`xingjian-agent/README.md`](../xingjian-agent/README.md) | API、配置项、部署与常见问题 |
| [`docs/README.md`](README.md) | 研究证据与设计取舍的来源 |
