# 重排实验室 · 前端交互 Demo

拖拽与 AI 对话协商的日历重排原型。纯前端、零依赖，直接打开 `index.html` 即可。

## 交互

- **拖动时间块**：按住事件主体上下拖动，15 分钟吸附；无冲突直接生效。
- **拖动上下边缘**：改变开始时间或时长；最短 15 分钟。
- **产生冲突**：事件变红、状态条提示，右侧 AI 自动收到冲突并给出方案。
- **在对话里改方案**：输入或点快捷 chip，例如
  - `别动客户会`
  - `把专注块缩短到 45 分钟`
  - `换到明天下午`
  - `为什么`
  - `重来`
- **应用 / 保留 / 备选**：每个方案卡有主方案、2–3 个备选、账单和解释。
- **画像**：左侧 P1–P8 切换；同一冲突会给出不同决定。
- **撤销 / 重置**：左上角；应用和直接拖动都可撤销。

## 文件

| 文件 | 用途 |
| --- | --- |
| `index.html` | 页面结构 |
| `style.css` | WarmGrid 视觉：暖纸、墨色侧栏、深色 AI rail、紫/青柠强调 |
| `planner.js` | 纯函数重排内核：冲突、候选、画像打分、方案、约束 |
| `app.js` | 拖拽、缩放、聊天、应用/撤销、渲染 |
| `test_planner.js` | Node 单元测试 |

## 跑测试

```bash
node test_planner.js
node --check planner.js
node --check app.js
```

## 接入现有 `xingjian-agent`

1. 把 `planner.js` 作为纯策略模块引入；`Rescheduler.propose()` 接受 `base / draft / draggedId / persona / constraints`。
2. 把 `app.js` 里的拖拽与聊天渲染移植到 `xingjian-agent/web/agent.js` 的日历与 AI 面板。
3. 应用方案时，把 `plan.schedule` 映射回 `data.schedule`；或把 `plan.changeList` 转成 `protocol.py` 的 `move_event / add_event / remove_event`。
4. 画像参数来自 `sched_profile.scheduling_params()`，替换 `PERSONAS` 的默认权重即可。

## 注意

- 当前是演示数据，不连接真实日历；刷新会重置。
- 所有重排都在本地确定性策略里完成，不调用 LLM；LLM 可以后续接在“把方案写成人话”这一层。
- `planner.js` 的权重是先验值；真实使用前应用 7 天日程校准。
