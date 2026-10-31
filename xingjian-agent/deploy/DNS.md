# 把 calhub.asia 指到服务器（DNSPod）

`calhub.asia` 在 **腾讯云 DNSPod** 注册（2026-10-31 注册成功，到期 2027-10-31），
NS 是 `ant.dnspod.net` / `tenth.dnspod.net`。

## 先说结论：现在解析不了，不是部署的问题

RDAP 查到的域名状态是：

```
status: server hold, add period
```

`server hold` 是**注册局层面的暂停解析**。它没解除之前，不论服务器部署得多完整，
浏览器都会得到 `NXDOMAIN`（我这边实测：`Resolve-DnsName calhub.asia` → DNS name does not exist）。

刚注册的 `.asia` 域名出现 `server hold`，通常是下面两种之一：

1. **实名认证 / 注册信息核验未完成** —— DNSPod 控制台首页会有「待实名认证」提示，
   提交身份证/营业执照后一般几分钟到几小时解除；
2. **ICANN 注册人邮箱验证** —— 注册当天会收到一封来自 `noreply@nameadmin.cn` /
   `verify@internic.net` 的邮件，点里面的链接确认（15 天内不确认会被强制 hold）。

处理完再看状态，确认恢复：

```powershell
# 返回记录里出现 A 就说明解析通了
Resolve-DnsName calhub.asia -Type A
```

```python
# 或者用 RDAP 看 status 里还有没有 server hold
python -c "import json,urllib.request;d=json.load(urllib.request.urlopen('https://rdap.org/domain/calhub.asia'));print(d['status'])"
```

## DNSPod 记录怎么填

在 DNSPod 控制台 → 我的域名 → `calhub.asia` → 记录管理：

| 主机记录 | 类型 | 记录值 | TTL | 说明 |
| --- | --- | --- | --- | --- |
| `@` | A | `<服务器公网 IP>` | 600 | 主域名 |
| `www` | CNAME | `calhub.asia` | 600 | 可选；加了再把 Caddyfile 里的 www 段取消注释 |

验证：

```powershell
Resolve-DnsName calhub.asia -Type A -Server 1.1.1.1
```

> 改完 DNS 一般 1–10 分钟生效（TTL 600）。Caddy 拿到解析之后会自动申请证书，
> 所以**先配 DNS，再跑 bootstrap**，否则证书签发会失败。

## 服务器选哪种

域名在腾讯云，用**腾讯云轻量应用服务器**最省事，但要注意备案：

| 机房 | 需要 ICP 备案 | 说明 |
| --- | --- | --- |
| 中国大陆（广州/上海/北京） | **需要** | 备案要 1–3 周，赶不上演示就不要选 |
| 中国香港 / 新加坡 | 不需要 | 延迟 30–60ms，演示够用，推荐 |

系统镜像选 **Ubuntu 22.04 或 24.04**，1 核 2G 即可。安全组放行 **80 / 443 / 22**。

## 部署（服务器上执行）

```bash
# 1. 建目录并上传（本机执行，把 IP 换成你的；结尾的 /. 连隐藏文件一起传）
ssh root@<服务器IP> 'mkdir -p /opt/xingjian'
scp -r xingjian-agent/. root@<服务器IP>:/opt/xingjian/

# 2. 在服务器上跑一键脚本
ssh root@<服务器IP>
sudo bash /opt/xingjian/deploy/bootstrap.sh calhub.asia
```

脚本会做：装 Docker → 写 `.env`（会问 DeepSeek key）→ `docker compose up -d --build`
→ 装 Caddy → 写 `Caddyfile`（含 `flush_interval -1`，保证 AI 流式不被缓冲）
→ 自动签证书 → 冒烟测试。

## 部署完怎么验收

在**任意一台能上网的机器**（包括这台笔记本）上：

```powershell
python scripts/verify_deploy.py calhub.asia
```

它会依次检查：DNS 是否解析、443 是否通、证书是否有效、首页是否返回登录页、
`/api/health` 与 `/api/agent/health` 是否正常、未登录访问 `/api/auth/me` 是否返回 401。

全部通过时输出 `ALL CHECKS PASSED`，退出码 0。

## 常见故障对照

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `NXDOMAIN` | 域名 `server hold`，或 A 记录没加 | 先按本文第一节解除 hold，再加 A 记录 |
| 证书一直失败 | DNS 没指向这台机器 / 80 端口没放开 | `dig +short calhub.asia` 对比 IP；云控制台放行 80 |
| 502 Bad Gateway | 容器没起来 | `docker compose logs --tail 60` |
| 登录后立刻退出 | `COOKIE_SECURE=1` 但用 http 访问 | bootstrap 已设 HTTP 跳转 HTTPS，确认访问的是 `https://` |
| AI 面板显示「离线演示」 | `.env` 里 `DEEPSEEK_API_KEY` 为空 | 填 key 后 `docker compose up -d` 重启 |

## 手动方式（不用脚本）

```bash
cd /opt/xingjian
cp .env.example .env && vi .env      # 填 key，改 COOKIE_SECURE=1 / XINGJIAN_TRUST_PROXY=1
docker compose up -d --build
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i 's/example.com/calhub.asia/g' /etc/caddy/Caddyfile
sudo systemctl reload caddy
```
