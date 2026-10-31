https://xingjian.onrender.com

# 行间

> 计划 → 反思 原型：待办、目标、日历、日 / 周反思，外加一个只给建议、不替你做决定的 AI 面板。

## 能做什么

- **今天 / Project**：待办、目标、里程碑与下一步
- **日反思 / 周反思**：先记事实，再写想法
- **AI 面板**：DeepSeek 流式对话，给出建议卡片；点「采用」才真正改日程
- **排布画像**：从你的记录里学块长、时段、缓冲，只调排布参数，不动任务与目标
- **账号与同步**：注册、登录、改密、退出所有设备、删号；每个账号一份服务端数据

## 本地运行

静态演示：双击 `index.html`，选「本地演示模式」。

完整版（账号 + AI）：

```powershell
cd xingjian-agent
python server.py
```

打开 <http://127.0.0.1:8000> 注册即可。Python 3.10+，不用装依赖；不填 `DEEPSEEK_API_KEY` 时 AI 显示离线，其余功能照常。

## 目录

| 路径 | 说明 |
| --- | --- |
| `index.html` | 静态版入口；GitHub Pages 发布根目录下的 html / css / js |
| `agent.js` / `agent.css` | AI 面板 |
| `auth.js` / `auth.css` | 登录、账号、同步 |
| `revision.css` … `unify.js` | V0.13 设计的 11 个增量层（首页 / 目标 / 反思 / 信息卡 / 侧栏 / 工作区） |
| `warmgrid-skin.css` | 视觉皮肤：暖纸底、墨色主操作、薰衣草/青柠强调，只改样式不改布局 |
| `xingjian-agent/` | Python 后端：账号、数据、画像规则、DeepSeek 代理、部署 |
| `calendar-rescheduler/` | 日历重排前后端设计包：拖拽/对话 Demo + 画像重排策略 |

前端无框架、无构建；后端纯 Python 标准库。

## 日历重排设计

- [交互 Demo](calendar-rescheduler/frontend/index.html)：拖动时间块、改时长、冲突后在 AI 对话里协商
- [重排策略](calendar-rescheduler/backend/STRATEGY.md)：P1–P8 画像、呼吸层、留白/日容量、A/B/C 备选
- [包说明](calendar-rescheduler/README.md)

## 更多

配置项、API、Docker / nginx / systemd 部署与测试见 [xingjian-agent/README.md](xingjian-agent/README.md)。

---

仓库名 `Happy-Halloween` 只是代号，项目本身叫「行间」。
