# 行间 · 登录、账号与部署包

行间是一个「计划 → 反思」原型：待办、目标、日历、日/周反思，加上一个只给建议、
不自动改日程的 AI 面板。这个目录在原型之外补了三件事：

1. **账号系统**：注册、登录、退出、修改显示名、修改密码、退出所有设备、删除账号。
   注册必须绑定并验证邮箱；登录仍然只用用户名和密码；忘记密码通过邮箱验证码重置。
2. **服务端数据**：每个账号一份 JSON 文档，保存在服务器文件夹里，换设备登录也能继续。
3. **可部署**：纯 Python 标准库，附带 Docker、nginx、Caddy、systemd 配置。

前端统一在 `web/index.html`，视觉和交互没有重做；账号接入只增加了 `auth.js` / `auth.css`，
并把原来的 `localStorage` 读写接到账号 API。AI 面板仍由 `agent.js` 接管。

---

## 1. 本地 30 秒启动

要求：Python 3.10+（推荐 3.12），**不需要 `pip install`**。

```powershell
cd xingjian-agent
python server.py
```

打开 <http://127.0.0.1:8000>，第一次会看到登录页，点「注册」创建账号即可。
注册需要邮箱验证码，所以先在 `.env` 里填好 `SMTP_PASSWORD`（QQ 邮箱授权码），
或者临时设 `MAIL_TRANSPORT=console`，验证码会打印在启动终端里。

- 不配 `DEEPSEEK_API_KEY` 也能用：AI 面板显示「AI 离线」，其他功能正常。
- 想让 AI 真实回复：`Copy-Item .env.example .env`，填入 key 后重启。
- 离线演示 AI：`$env:AGENT_MOCK='1'; python server.py`。
- 换端口：`$env:PORT='8080'; python server.py`。

也可以双击 `web/index.html`：页面会提示连不上服务，并给出「先以本地演示模式打开」。
这个模式只写浏览器 `localStorage`，不创建账号、不跨设备同步。

---

## 2. 数据保存在哪里

本地默认目录就是 **`E:\UNNC_Y1_FAM\黑客松\server information`**（`xingjian-agent`
的上一级），启动日志会打印实际路径：

```text
[agent] 数据目录：E:\UNNC_Y1_FAM\黑客松\server information
```

```text
server information/
├─ accounts.json          账号、显示名、PBKDF2 密码哈希、session_version
├─ sessions.json          session token 的 SHA-256 哈希与过期时间
├─ data/
│  └─ u_<16 hex>.json     每个账号一份，结构和 localStorage 里的 data 相同
└─ audit.log              注册、登录、改密、删号等事件（JSONL，自动轮转）
```

- 密码只存 **PBKDF2-HMAC-SHA256 / 600,000 次 / 16 字节随机盐**，不存明文。
- 会话 token 只存 SHA-256 哈希；cookie 是 `HttpOnly` + `SameSite=Lax`，默认 7 天。
- 删除账号会同时删除 `data/<id>.json`、会话和账号记录，不能恢复。
- 数据目录建议只给运行服务的用户读写；不要放进静态网站的公开目录。

**备份**：停机后直接复制整个 `server information` 文件夹即可；Linux 上可用
`deploy/backup.sh`。

---

## 3. 账号功能

| 功能 | 位置 | 说明 |
| --- | --- | --- |
| 注册 | 登录页「注册」 | 用户名 3–32 位（字母/数字/`_` `.` `-`），邮箱必须验证，密码至少 8 位 |
| 邮箱验证码 | 注册表单、重置密码表单 | 6 位数字，10 分钟有效，60 秒后可重发，最多试 5 次 |
| 登录 / 退出 | 登录页、右上角头像 | **登录页只有用户名和密码，不显示邮箱输入框**；会话 cookie，7 天有效 |
| 忘记密码 | 登录页「忘记密码？」 | 用注册邮箱收验证码，验证后设置新密码；其他设备立即退出 |
| 修改显示名 | 头像 → 账号 | 不影响登录用户名 |
| 修改密码 | 头像 → 账号 | 需要当前密码；其他设备立即失效，当前设备保持登录 |
| 退出所有设备 | 头像 → 账号 | 清除该账号全部会话 |
| 删除账号 | 头像 → 账号 | 输入密码确认，删除账号和全部数据 |
| 自动同步 | 顶栏「已同步 / 保存中…」 | 每次改动先写本地缓存，再 500 ms 防抖写服务器 |
| 关闭注册 | `XINGJIAN_ALLOW_REGISTRATION=0` | 建议建好账号后关闭 |

数据冲突策略是**后写覆盖**（last write wins）。同一个账号在两个标签页同时编辑时，
后保存的一方会覆盖前一方；原型阶段够用，正式多人使用建议加版本号。

---

## 4. 配置项

复制 `.env.example` 为 `.env`，或直接设环境变量。环境变量优先于 `.env`。

### 服务与账号

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | Docker / 服务器上改 `0.0.0.0` |
| `PORT` | `8000` | 监听端口 |
| `XINGJIAN_DATA_DIR` | `../server information` | 数据目录；Docker 中为 `/data` |
| `XINGJIAN_SESSION_DAYS` | `7` | 登录有效期（1–365 天） |
| `COOKIE_SECURE` | `0` | HTTPS 部署必须改 `1`；本地 http 保持 `0` |
| `XINGJIAN_ALLOW_REGISTRATION` | `1` | `0` 关闭公开注册 |
| `XINGJIAN_MAX_USERS` | `5000` | 账号数量上限 |
| `XINGJIAN_TRUST_PROXY` | `0` | 在 nginx/Caddy 后面改 `1`，限流才能拿到真实 IP |

### 邮箱验证（QQ Mail）

注册验证码和「忘记密码」都通过 SMTP 发信。发送邮箱是 **3034700847@qq.com**，
配置只写在 **`xingjian-agent/.env`**（Docker 由 `env_file` 读入，代码里没有硬编码密码）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MAIL_TRANSPORT` | `smtp` | `smtp` 真发信；`console` 只把验证码打印到启动终端 |
| `SMTP_HOST` | `smtp.qq.com` | QQ 邮箱 SMTP 服务器 |
| `SMTP_PORT` | `465` | SSL 用 465，STARTTLS 用 587 |
| `MAIL_SECURITY` | `ssl` | `ssl` / `starttls` / `plain` |
| `SMTP_USER` | 空 | 登录 SMTP 的完整邮箱：`3034700847@qq.com` |
| `SMTP_PASSWORD` | 空 | **QQ 邮箱授权码**，不是邮箱登录密码；填这里 |
| `MAIL_FROM` | 同 `SMTP_USER` | 发件人地址；QQ 要求与登录邮箱一致 |
| `MAIL_FROM_NAME` | `行间` | 收件人看到的发件人名称 |
| `SMTP_TIMEOUT` | `20` | 秒 |
| `EMAIL_CODE_TTL` | `600` | 验证码有效期（秒） |
| `EMAIL_CODE_RESEND` | `60` | 同一邮箱重发间隔（秒） |
| `EMAIL_CODE_MAX_ATTEMPTS` | `5` | 单个验证码最多尝试次数 |
| `EMAIL_CODE_MAX_PER_HOUR` | `5` | 同一邮箱每小时最多发几封 |

**授权码怎么拿**：

1. 用 3034700847@qq.com 登录 <https://mail.qq.com>。
2. 设置 → 账户 → POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV 服务。
3. 开启「POP3/SMTP 服务」，按提示生成**授权码**。
4. 打开 `xingjian-agent/.env`，把授权码填在 `SMTP_PASSWORD=` 后面，重启服务。

`SMTP_PASSWORD` 只存在服务端，浏览器和 API 响应都拿不到。没有授权码时，
`/api/health` 的 `email` 会是 `false`，注册页会提示「邮箱服务未配置」。
只想本地跑通流程时，把 `MAIL_TRANSPORT` 改成 `console`，验证码会出现在运行
`python server.py` 的终端里。

### DeepSeek（AI 面板）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 空 | 只存在服务端，浏览器拿不到 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容入口 |
| `DEEPSEEK_MODEL` | `deepseek-flash` | 也接受 `deepseek-v4-pro`、`deepseek-chat`、`deepseek-reasoner`；用 `scripts/check_key.py` 查这个 key 到底能用哪些 |
| `AGENT_MOCK` | `0` | `1` = 离线示例回复 |
| `AGENT_TEMPERATURE` | `0.4` | |
| `AGENT_MAX_TOKENS` | `1200` | |
| `AGENT_TIMEOUT` | `60` | 秒 |
| `AGENT_RATE_LIMIT` | `40` | 每 IP 每分钟请求数 |
| `AGENT_MAX_HISTORY` | `8` | 回放的历史消息条数 |
| `AGENT_CORS_ORIGINS` | `null,http://127.0.0.1,http://localhost` | 只在跨域打开 agent 时需要 |

---

## 5. 前端是怎么接上的

`web/index.html` 在原型的 3 处接入之后，又加了 4 个小改动：

1. 顶部引入 `auth.css`，body 增加 `xj-booting` 类，首屏先等登录检查。
2. 顶部右侧增加两个状态 pill（AI / 同步）和账号按钮。
3. `let data` 从 `XJ.dataKey()` 读取；`save()` 仍写 `localStorage`，并调用
   `XJ.queueSave()` 防抖同步到服务器。
4. 去掉启动时的裸 `render()`；由 `auth.js` 在 `/api/auth/me` 和 `/api/data`
   都成功后再渲染。

`auth.js` 以覆盖 `render` 的方式在每次渲染后刷新用户名和设置页的「账号与安全」，
不侵入原型的目标/反思逻辑。`agent.js` 的聊天记录按账号分 key 保存，换账号不会串。

**关于 `revision.css` / `examples.css` / `revision.js` / `examples.js`：**
这四个文件已经随 `web/` 一起提供，`index.html` 里的引用可以直接生效；`warmgrid-skin.css` 和 `edge-theme.js` 也在同一目录。

---

## 6. API

所有写接口都要求 `Content-Type: application/json`，并校验同源
（`Origin` / `Referer` / `Sec-Fetch-Site`）。除注册、登录、发验证码、重置密码
和健康检查外都需要会话 cookie。

```http
POST   /api/auth/email-code      {email, purpose: "register"|"reset", username?}
POST   /api/auth/register        {username, password, display_name?, email, code}
POST   /api/auth/login           {username, password}
POST   /api/auth/password/reset  {email, code, new_password}
POST   /api/auth/logout          {}
GET    /api/auth/me              -> {user} | 401
PATCH  /api/account              {display_name}
POST   /api/account/password     {current_password, new_password}
POST   /api/account/logout-all   {}
DELETE /api/account              {password}

GET    /api/data                 -> {data, updated_at}
PUT    /api/data                 {data}
GET    /api/health               -> {ok, service, registration, email}
```

邮箱验证码的规则：

- `purpose=register` 时，`email` 不能已被注册；建议同时传 `username`，这样用户名被占用会在发码前报错。
- `purpose=reset` 时，无论邮箱是否存在都返回同一句提示，避免被用来枚举账号；只有已注册邮箱才会真的收到信。
- 验证码只存在内存里，服务重启后全部失效；重新获取即可。
- 验证码绑定用途，注册码不能拿去重置密码。

示例：

```bash
# 1. 发送注册验证码（本地控制台模式可在终端看到 code）
curl -i -c cookies.txt -H 'Content-Type: application/json' \
  -d '{"email":"alice@example.com","purpose":"register","username":"alice"}' \
  http://127.0.0.1:8000/api/auth/email-code

# 2. 用邮件里的 6 位 code 注册
curl -i -c cookies.txt -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"correct-horse","display_name":"Alice","email":"alice@example.com","code":"123456"}' \
  http://127.0.0.1:8000/api/auth/register

# 3. 登录仍然只用用户名和密码
curl -i -c cookies.txt -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"correct-horse"}' \
  http://127.0.0.1:8000/api/auth/login

curl -b cookies.txt http://127.0.0.1:8000/api/data
```

AI 接口路径不变，但 `/chat` 和 `/report` 也要求登录，避免匿名请求消耗
DeepSeek 额度（健康检查仍公开）：

```http
GET  /api/agent/health
POST /api/agent/chat      -> text/event-stream   (需要登录)
POST /api/agent/report    -> {report}            (需要登录)
```

---

## 7. 部署到服务器

### 7.0 一键脚本（Ubuntu / Debian）

先把域名 A 记录指到服务器（域名还在 `server hold` 时先看 `deploy/DNS.md`），
上传整个项目后执行：

```bash
# 本机上传（注意结尾的 /.，连 .env.example 等隐藏文件一起传）
scp -r xingjian-agent/. root@<服务器IP>:/opt/xingjian/

# 服务器上
sudo bash /opt/xingjian/deploy/bootstrap.sh calhub.asia
```

脚本会安装 Docker 与 Caddy、生成 `.env`（会询问 DeepSeek key 和 QQ 邮箱授权码）、
构建并启动容器、申请证书，最后检查 `https://<域名>/api/health`。可重复执行；
已有的 `.env` 只会更新
`HOST` / `COOKIE_SECURE` / `XINGJIAN_TRUST_PROXY` / `AGENT_CORS_ORIGINS`，
并在缺少时补上 SMTP 默认项（不会覆盖已有的 `SMTP_PASSWORD`）。

部署后从任意机器验收：

```bash
python scripts/verify_deploy.py calhub.asia
```

### 7.1 Docker + Caddy（手动，自动 HTTPS）

```bash
cd xingjian-agent
cp .env.example .env
# 编辑 .env：DEEPSEEK_API_KEY、COOKIE_SECURE=1、XINGJIAN_TRUST_PROXY=1
mkdir -p data
sudo chown -R 10001:10001 data     # 容器内 app 用户的 uid
docker compose up -d --build
docker compose logs -f
```

`docker-compose.yml` 默认只绑定 `127.0.0.1:8000`，由 Caddy 对外提供 HTTPS：

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i 's/example.com/你的域名/' /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy 会自动申请证书；`flush_interval -1` 保证 AI 的 SSE 不被缓冲。

### 7.2 Docker + nginx

同样先启动容器，然后：

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/xingjian
sudo sed -i 's/example.com/你的域名/g' /etc/nginx/sites-available/xingjian
# 首次签发证书（nginx 未占用 80 端口时）：
sudo certbot certonly --standalone -d 你的域名
sudo ln -s /etc/nginx/sites-available/xingjian /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

nginx 配置已关闭 `proxy_buffering` 并放宽了 `proxy_read_timeout`，否则 AI 流式回复
会变成一次性吐出。**HTTPS 下务必**把 `.env` 里的 `COOKIE_SECURE` 改成 `1`，否则
浏览器不会保存会话 cookie。

### 7.3 systemd（不用 Docker）

```bash
sudo useradd --system --home /opt/xingjian --shell /usr/sbin/nologin xingjian
sudo mkdir -p /opt/xingjian /etc/xingjian
sudo cp -r ./* /opt/xingjian/
sudo cp .env.example /etc/xingjian/xingjian.env
sudo nano /etc/xingjian/xingjian.env   # XINGJIAN_DATA_DIR=/var/lib/xingjian
sudo chown -R xingjian:xingjian /opt/xingjian
sudo cp deploy/xingjian.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now xingjian
sudo journalctl -u xingjian -f
```

单元文件通过 `StateDirectory=xingjian` 自动创建 `/var/lib/xingjian`（属主为
`xingjian`），并启用了 `ProtectSystem=strict`、`PrivateTmp` 等基本加固。

### 7.4 上线检查清单

- [ ] 全站 HTTPS，`COOKIE_SECURE=1`
- [ ] 反代后 `XINGJIAN_TRUST_PROXY=1`
- [ ] 建好账号后 `XINGJIAN_ALLOW_REGISTRATION=0`
- [ ] 数据目录不在 Web 根目录，权限 `0700`
- [ ] 防火墙只开放 80/443，8000 仅本机
- [ ] 定期备份 `server information` / `/data`
- [ ] 填好 `DEEPSEEK_API_KEY` 后确认 `/api/agent/health` 返回 `configured: true`

---

## 8. 验证

```powershell
python -m unittest discover -s tests   # 后端测试：账号、邮箱验证、Agent、画像、策略
node tests/dom_smoke.mjs               # 22 项 agent 前端链路
node tests/auth_smoke.mjs              # 17 项登录/注册/找回密码前端链路
python scripts/verify_deploy.py 你的域名  # 部署后从外部检查 DNS/TLS/登录页/API
```

后端测试覆盖账号存储、会话失效、改密踢下线、数据隔离、删除账号、跨站拒绝和
HTTP 全链路；前端冒烟测试用 DOM 桩跑真实 JS，不能替代浏览器看样式，但能挡住接线错误。

---

## 9. 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| 登录后刷新又回到登录页 | 本地 http 下 `COOKIE_SECURE=1` 会让浏览器拒收 cookie；改回 `0` |
| 顶栏一直「未同步」 | 服务端连不上，或 `.env` 的 `XINGJIAN_DATA_DIR` 无写权限；看日志 |
| 启动报「数据目录不可用」 | 目录被占用/无权限；或 `accounts.json` 损坏（先备份再处理） |
| 端口被占用 | `$env:PORT='8080'; python server.py` |
| AI 面板显示「AI 离线」 | 没配 `DEEPSEEK_API_KEY`；或 `AGENT_MOCK=1` |
| 忘记密码 | 登录页点「忘记密码？」，用注册邮箱收验证码重置；旧账号没有邮箱时无法自助找回 |
| 注册时提示「邮箱服务未配置」 | `.env` 的 `SMTP_PASSWORD` 为空；填 QQ 邮箱授权码后重启，或临时用 `MAIL_TRANSPORT=console` |
| 收不到验证码 | 先看垃圾邮件；确认 `SMTP_USER`/`MAIL_FROM` 都是 3034700847@qq.com，且 QQ 邮箱已开启 SMTP 服务 |
| 发信报 535 / 授权码无效 | `SMTP_PASSWORD` 填的是 QQ 登录密码，不是授权码；重新生成授权码 |
| 重启服务后旧验证码不能用 | 验证码只在内存里，重启即失效；重新点「发送验证码」 |
| 登录页找不到邮箱输入框 | 这是有意的：登录只用用户名 + 密码，邮箱只用于注册验证和找回密码 |
| 多实例部署 | 文件存储和验证码都是单写者设计，不要开多个容器/worker 指向同一目录 |

---

## 10. 已知边界

- 没有 OAuth、管理员后台；账号是自服务模型。邮箱验证和找回密码已经支持。
- 邮箱验证码存在内存里，只适合单进程部署；多实例需要换成 Redis 或数据库。
- 旧账号（升级前创建、没有 `email` 字段）仍可用用户名密码登录，但暂时不能自助找回密码；重新注册一个新账号即可。
- 文件存储适合演示、小团队内测；要扛并发请换数据库，并把 `AccountStore` 抽成接口。
- 数据保存是后写覆盖，不做字段级合并。
- AI 对话仍存在浏览器（按账号分 key），不跨设备同步；只有计划/反思数据在服务器。
- 原型里 `revision.css` 等四个文件缺失，页面依赖内联样式；补上后会更完整。

---

## 11. 目录结构

```text
xingjian-agent/
├─ server.py            HTTP 服务：静态托管 + 账号/邮箱验证 API + agent 代理
├─ accounts.py          账号、会话、PBKDF2、邮箱唯一性、按用户数据文件
├─ mailer.py            QQ Mail SMTP 发信 + 控制台验证码模式
├─ verification.py      6 位验证码：哈希存储、有效期、重发间隔、尝试次数
├─ config.py            .env / 环境变量
├─ deepseek.py          DeepSeek 流式客户端 + 正文/提案分离器
├─ protocol.py          SSE 编码 + 提案白名单校验
├─ prompts.py           系统提示词 + 应用状态序列化
├─ sched_profile.py     排布画像：后验指标、漂移检测、姿态、写入校验
├─ policy.py            输出策略：正向框架、事实不省略、危机拦截
├─ mock_agent.py        离线示例回复
├─ web/
│  ├─ index.html        唯一前端入口（auth / agent / edge-theme / pages-bootstrap）
│  ├─ auth.js           登录、账号管理、服务端同步
│  ├─ auth.css          登录页、同步状态、账号弹窗样式
│  ├─ agent.js          DeepSeek 面板桥接（按账号隔离聊天记录）
│  ├─ agent.css         AI 卡片 / 状态点 / 打字动画
│  ├─ edge-theme.js     Edge Fluent 外观切换（浅/深/系统、5 色、10 图像）
│  ├─ planner.js        日历重排内核（冲突 / 候选 / 画像打分 / 备选）
│  ├─ home-reschedule.js Home 时间块拖拽、缩放、冲突后在 AI 面板协商
│  ├─ home-reschedule.css 拖拽手柄与重排方案卡样式
│  └─ pages-bootstrap.js GitHub Pages 无后端时进入本地演示模式
├─ docs/
│  └─ profile-rules.md  画像与 Agent 行为规则（条款编号 P-0..P-10）
├─ tests/               离线测试（含 test_sched_profile.py、test_verification.py）
├─ scripts/
│  ├─ verify_deploy.py  部署后从外部检查 DNS / TLS / 登录页 / API
│  └─ profile_demo.py   七轮对话演示：画像如何被写入、拒绝、漂移
├─ deploy/
│  ├─ bootstrap.sh      Ubuntu/Debian 一键部署（Docker + Caddy + 证书）
│  ├─ DNS.md            calhub.asia 的 DNSPod / server hold 排查记录
│  ├─ nginx.conf        nginx 反代示例（含 SSE 配置）
│  ├─ Caddyfile         Caddy 自动 HTTPS 示例
│  ├─ xingjian.service  systemd 单元
│  └─ backup.sh         数据目录备份
├─ Dockerfile
├─ docker-compose.yml
├─ start.ps1 / start.sh
└─ .env.example
```

---

## 12. 排布画像（Agent 行为规则）

规则文档在 `docs/profile-rules.md`（条款编号 P-0..P-10、7.x）。**这些规则不是写在提示词里指望模型自觉，而是由代码逐条兜住：**

| 环节 | 位置 | 保证什么 |
| --- | --- | --- |
| 写入 | `sched_profile.validate_delta()` | 槽位白名单、值域、单轮 ≤3 条、`quote` 必须是用户原话的子串 |
| 存储 | `accounts.load_profile / save_profile` | 按账号存 `{user_id}.profile.json`，与浏览器数据分开，客户端覆盖不到 |
| 生效 | `prompts.chat_messages(profile_block=)` | 独立 `<persona>` 系统消息，只给参数不给标签 |
| 下一轮 | `sched_profile.advance()` | 本轮写入下一轮才生效（P-9） |
| 不回灌 | `server._record_profile()` | 模型的话不能当数据；只接收校验通过的增量 |
| 不进前端 | `server` 的 `persona` 事件 | 只在服务端消费，不转发浏览器 |

### 12.1 输出规则（5.6 / 5.7）

语气类规则没法靠提示词保证，所以全部由 `policy.py` 在服务端跑一遍再发出去：

| 规则 | 位置 | 做法 |
| --- | --- | --- |
| 禁 emoji、感叹号 | `policy.apply()` | 直接删/替换 |
| 鸡汤、冷话、比较式安慰、否定感受、伪造低评价 | `policy.apply()` | 按**分句**丢弃，不改写原句 |
| **带数字的分句永远保留** | `policy.apply()` | 事实优先于语气，只记违规 |
| 正文 ≤3 句 | `policy.apply(max_sentences=3)` | 截断；反思报告传 `None` 不受限 |
| 想一套说一套 | `policy.validate_draft()` | `draft` 里的 `predicted`/`risk` 只落日志，永不出现在正文 |
| 事实不可省略 | `policy.missing_facts()` + `append_missing()` | 草稿承诺的数字没出现，补一行中性「记录：…」，不改写原句 |
| 危机例外 | `policy.detect_crisis()` | 命中自伤表述时，整段替换为固定安全文案，不走正向框架 |

**代价（有意选择）**：正文先缓冲、过策略、再分片回放，所以首字延迟等于「模型生成完」。换来的是浏览器里不会出现违规句子——如果边流边改，用户会先看到「你又超时了」再被替换，那比慢更糟。

规则优先级固定为：**危机 > 事实 > 语气**。任何冲突都按这个顺序裁决。

可调参数集中在 `sched_profile.py` 与 `policy.py` 顶部：τ=21 天、`PRIOR_K`=5、漂移阈值 1.5σ/3×MAD、单轮 3 条、正文 3 句、回放 4 字/12ms。

验证：

```bash
python -m unittest discover -s tests    # 90 项，含 34 项画像 + 18 项输出策略
python scripts/profile_demo.py          # 七轮行为：写入、拒绝、漂移、押注
```

边界：画像只改排布参数（块长、时段、缓冲、措辞），不改供给（任务、目标、是否主动提议）；`scheduling_params()["proactive"]` 固定为 `L1`。
