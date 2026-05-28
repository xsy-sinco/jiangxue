# 部署到腾讯云/阿里云轻量服务器

目标：在一台 ¥24-99/年的国内云服务器上 24/7 跑你的内战数据网页，队友直接用 `http://<服务器IP>` 访问。

预估时间：**首次约 30 分钟**（含买服务器、装 Docker）。

---

## 1. 买一台轻量服务器

任选一家，配置都够：

### 腾讯云轻量
- 入口：<https://cloud.tencent.com/product/lighthouse>
- 推荐：**2 核 2G 内存 / 4M 带宽 / 60G SSD**，年付。新人活动经常 ¥39-99/年。
- 镜像：选 **Ubuntu 22.04 LTS**（其他 Linux 也行，本指南以 Ubuntu 为例）
- 地域：**国内队友选广州/上海/北京**；**海外队友也要看可选香港**。

### 阿里云轻量
- 入口：<https://www.aliyun.com/product/swas>
- 同上规格。镜像选 Ubuntu 22.04。

> 💡 **避坑**：选好后控制台 → 防火墙/安全组 → **放行 80 端口（TCP）**。这是云厂商的网络防火墙，必须在控制台放行，否则即使容器跑起来也连不上。

---

## 2. SSH 登上服务器

```powershell
# Windows PowerShell（Win10/11 自带 ssh）
ssh ubuntu@<你的服务器公网IP>
# 输入密码（控制台首次设的）或选 key 登录
```

服务器是 root 用户登录的话，把下面命令里的 `sudo` 都去掉。

---

## 3. 装 Docker（一次性，约 3 分钟）

```bash
# 用 Docker 官方安装脚本
curl -fsSL https://get.docker.com | sudo sh

# 让当前用户能直接 docker 命令（免 sudo）
sudo usermod -aG docker $USER
newgrp docker     # 立即生效，不用重连

# 验证
docker --version
docker compose version
```

---

## 4. 把代码传上去

有两种方式，二选一。

### 方式 A：推到 GitHub 再 clone（推荐）

在你的 Windows 本地：

```powershell
cd E:\Sinco\project\dota-inhouse-stats

# 检查 .gitignore 已生效（保证 config.json 不被推送）
git init
git add .
git status              # 确认 config.json 不在列表里！如果在，立即停！
git commit -m "init"

# 在 https://github.com/new 建个仓库后
git remote add origin https://github.com/<你>/<repo>.git
git push -u origin main
```

服务器上：

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/<你>/<repo>.git dota-stats
cd dota-stats
```

### 方式 B：scp 直接传（不想用 git）

Windows 本地 PowerShell：

```powershell
cd E:\Sinco\project
# 排除掉敏感文件和大目录
$exclude = @('config.json', 'matches.txt', '.env', 'data', 'output', '__pycache__', '.git', '.venv')
# 简单做法：先压缩
tar.exe --exclude='dota-inhouse-stats/config.json' `
        --exclude='dota-inhouse-stats/data' `
        --exclude='dota-inhouse-stats/output' `
        --exclude='dota-inhouse-stats/__pycache__' `
        --exclude='dota-inhouse-stats/.git' `
        -czf dota-stats.tar.gz dota-inhouse-stats

scp dota-stats.tar.gz ubuntu@<IP>:~
```

服务器上：

```bash
tar xzf dota-stats.tar.gz
mv dota-inhouse-stats dota-stats
cd dota-stats
```

---

## 5. 创建 .env 文件

```bash
cp .env.example .env
nano .env       # 或 vi
```

填入：

```
LEAGUE_ID=19479
STEAM_API_KEY=你的32位STEAM_KEY
OPENDOTA_API_KEY=
RATE_LIMIT_PER_MINUTE=55
```

保存退出（nano：Ctrl+O → Enter → Ctrl+X）。

> ⚠️ **重要安全提醒**：`STEAM_API_KEY` 是你的私钥。放在服务器 `.env` 文件里安全（文件权限默认仅当前用户可读），**但永远不要 push 到公开 git 仓库或截图发出去**。`.gitignore` 已经把 `.env` 屏蔽了。

---

## 6. 启动！

```bash
docker compose up -d --build
```

第一次会构建镜像 + 下载 Python 依赖，约 2-3 分钟。看日志：

```bash
docker compose logs -f
# 看到 [INFO] Listening at: http://0.0.0.0:8000 就是起来了
# 之后会看到 GET / 200 等访问日志
# Ctrl+C 退出日志（容器还在跑）
```

容器内会自动启动后台线程拉取 league 数据；500 场详情首次拉取需要约 9 分钟（受 OpenDota 限速 55req/min）。期间网页可以打开，进度条会实时显示。

---

## 7. 配置 Linux 防火墙（如果开了 ufw）

```bash
sudo ufw status
# 如果是 active：
sudo ufw allow 80/tcp
sudo ufw reload
```

腾讯云/阿里云的默认镜像通常没开 ufw，跳过这步即可。

---

## 8. 访问

浏览器打开：

```
http://<你的服务器公网IP>
```

把这个 URL 直接发给队友就行。

---

## 常用运维命令

```bash
# 查状态
docker compose ps

# 看实时日志
docker compose logs -f

# 重启（改了 .env 或代码后）
docker compose restart

# 停止
docker compose down

# 更新代码后重新部署
git pull            # 或重新 scp 上传
docker compose up -d --build

# 清空缓存重新拉所有比赛（一般不用）
docker compose down
sudo rm -rf data/matches/* data/aggregate.json
docker compose up -d
```

---

## 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| 浏览器一直转圈打不开 | 80 端口没在云控制台放行 | 控制台 → 防火墙 → 添加规则 → TCP 80 → 0.0.0.0/0 |
| 能 ping 通 IP 但 80 端口不通 | 同上，或服务器内 ufw 也拦了 | `sudo ufw allow 80/tcp` |
| 网页打开了但永远显示"还没有数据" | 后台拉取出错 | `docker compose logs --tail 100` 看报错；常见是 STEAM_API_KEY 写错 |
| Steam 返回 403 | API key 错 / 失效 | 重新到 <https://steamcommunity.com/dev/apikey> 看 key 是否还在 |
| 容器反复重启 | 应用启动失败 | `docker compose logs` 看 traceback，多半是 config 缺字段 |
| 内存爆 | 单 worker 5 万人同时刷 | 把 `Dockerfile` CMD 里的 `--threads 4` 调到 `--threads 8`；或加 swap |

---

## 怎么续期 / 扩展

- **加域名**：买个 ¥40/年的域名（阿里云/腾讯云），DNS A 记录指向服务器 IP。然后在前面套个 Caddy 反代自动签 HTTPS 证书，把 docker-compose 加个 caddy 服务即可，告诉我可以再写一份。
- **国内备案**：用国内服务器 + 域名需要 ICP 备案（约 7-20 个工作日，免费）。香港服务器免备案。
- **多联赛**：现在代码是单 league 设计，要加多 league 需要小改 — 说一声即可。
- **自动每日刷新**：加个 cron 调用 `/api/refresh`，或者把 `init_app` 里的定时任务加上 `threading.Timer`。
