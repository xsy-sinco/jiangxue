# 阿里云完整部署指南

适用：**阿里云轻量应用服务器 SWAS**（也兼容 ECS 标准型，差别会单独标注）。
目标：30 分钟把内战数据网页跑在你的阿里云服务器上，队友用 `http://<公网IP>` 就能访问。

> ⚠️ **关于服务器地域**：如果你买的是**大陆机房**（华东/华北/华南），Steam Web API 的访问可能不稳定（路由偶尔抖）。本指南第 7 步会先把你本地已经拉好的 500 场缓存上传过去，**网页首屏不依赖 Steam，立刻有数据展示**。后续刷新若 Steam 通不到再换方案。如果你买的是**香港/新加坡机房**，Steam 完全稳定，按完整流程走即可。

---

## ✅ 部署前检查清单

请确认你已经有：
- 阿里云账号登录后能看到自己的服务器
- 服务器的**公网 IP**（在控制台实例详情页）
- 服务器的**登录密码**（如果没设过，控制台 → 实例详情 → 重置密码）
- 本地 `E:\Sinco\project\dota-inhouse-stats\config.json` 里 `steam_api_key` 已填好
- 本地 `data/matches/` 目录里有 500 个 `.json` 文件（之前跑过 `python -m src.main`）

---

## 第 1 步：阿里云控制台 — 放行 80 端口（**最关键，漏了一切白搭**）

阿里云轻量应用服务器有自己的"防火墙"页面，与 ECS 的"安全组"功能等价。

1. 登录阿里云控制台 → 顶部搜索 **"轻量应用服务器"**（或 ECS 实例列表）
2. 点你的实例 → 左侧 **"防火墙"** 标签
3. 点 **"添加规则"**：
   - 应用类型：**HTTP**（会自动填好协议=TCP、端口=80）
   - 备注：`dota-stats-web`
4. 保存

如果是 ECS（标准型，非轻量）：
- 实例列表 → 点实例 → **"安全组"** 标签 → "配置规则" → 入方向 → 添加 → 端口 80、源 `0.0.0.0/0`

> 💡 验证：本地命令行跑 `curl -v http://<公网IP>` 应该至少能建立 TCP（即使返回 404）。如果一直 timeout 就是防火墙没放行。

---

## 第 2 步：取一份阿里云 Docker 镜像加速器地址（一次性）

这一步让你的服务器拉 `python:3.11-slim` 等基础镜像不会慢成龟速。

1. 阿里云控制台 → 顶部搜索 **"容器镜像服务"** → 点进去
2. 左侧 **"镜像工具" → "镜像加速器"**
3. 复制你的专属加速器地址，形如：

   ```
   https://xxxxxxxx.mirror.aliyuncs.com
   ```

   （每个账号唯一，留着第 5 步用）

---

## 第 3 步：SSH 登录服务器

两种方式任选其一：

### 方式 A：用控制台 Workbench（最简单，浏览器内 SSH）

实例详情页右上角 → **"远程连接"** → "Workbench 远程连接"
- 用户名 `root`（轻量服务器默认就是 root）
- 密码：你刚才设的

### 方式 B：本地 PowerShell ssh

```powershell
ssh root@<你的公网IP>
# 第一次会问 yes/no，输 yes，然后输密码
```

> 后续命令都假设以 **root** 身份运行。如果你是 ubuntu 用户，所有命令前加 `sudo`。

---

## 第 4 步：装 Docker（约 3 分钟）

```bash
# 用阿里云镜像源安装 Docker（速度比 get.docker.com 快 10 倍）
curl -fsSL https://get.docker.com | bash -s docker --mirror Aliyun

# 启动 + 开机自启
systemctl enable --now docker

# 验证
docker --version
docker compose version
```

预期看到：
```
Docker version 24.x.x ...
Docker Compose version v2.x.x
```

---

## 第 5 步：配 Docker 镜像加速（用第 2 步拿到的地址）

```bash
# 创建配置文件
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://xxxxxxxx.mirror.aliyuncs.com",
    "https://docker.m.daocloud.io"
  ],
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF

# ↑ 把 xxxxxxxx.mirror.aliyuncs.com 改成你第 2 步复制的地址！

# 重启 Docker 应用配置
systemctl daemon-reload
systemctl restart docker

# 验证生效
docker info | grep -A 3 "Registry Mirrors"
```

---

## 第 6 步：把代码传到服务器

最快是用 scp 整包上传，不依赖 git。

### 在 **你的 Windows 本地** 打开 PowerShell：

```powershell
cd E:\Sinco\project

# 用 tar 打包，排除敏感文件和不需要的目录
# data/ 不排除 —— 我们要把已缓存的 500 场比赛带过去（关键！）
tar.exe `
  --exclude='dota-inhouse-stats/__pycache__' `
  --exclude='dota-inhouse-stats/.git' `
  --exclude='dota-inhouse-stats/output' `
  --exclude='dota-inhouse-stats/.venv' `
  --exclude='dota-inhouse-stats/*.log' `
  -czf dota-stats.tar.gz dota-inhouse-stats

# 看一下大小（含 500 场缓存大约 10-30 MB）
Get-Item dota-stats.tar.gz | Select-Object Length

# 上传（替换成你的 IP）
scp dota-stats.tar.gz root@<你的公网IP>:/root/
```

### 在 **服务器** 上：

```bash
cd /root
tar xzf dota-stats.tar.gz
mv dota-inhouse-stats dota-stats
cd dota-stats

# 看一眼缓存到位没
ls data/matches/ | wc -l   # 应该约 500
```

---

## 第 7 步：写 `.env` 配置

```bash
cp .env.example .env
nano .env
```

填入（注意把 KEY 真值粘进去）：

```
LEAGUE_ID=19479
STEAM_API_KEY=你那个32位的STEAM_KEY
OPENDOTA_API_KEY=
RATE_LIMIT_PER_MINUTE=55
```

Ctrl+O → Enter → Ctrl+X 保存退出。

> 💡 你本地的 `config.json` 也一并上传了，里面也有这个 key。这是 OK 的 —— **服务器目录只有 root 能访问**。但**永远不要**把这个目录 push 到公开 GitHub。

---

## 第 8 步：启动！

```bash
docker compose up -d --build
```

第一次构建约 1-3 分钟（pip 走阿里云镜像，python base image 走 Docker Hub 加速器）。

看日志确认启动成功：

```bash
docker compose logs -f --tail 50
```

应该能看到：

```
[INFO] Listening at: http://0.0.0.0:8000
```

按 Ctrl+C 退出日志查看（容器仍在跑）。

---

## 第 9 步：访问

浏览器打开：

```
http://<你的公网IP>
```

由于本地缓存的 500 场详情被一起带上来了，**首屏就有数据展示**（聚合在容器启动 1-5 秒内完成）。

把这个 URL 发给队友就行。

---

## 第 10 步：可选 — 验证 Steam API 在你服务器上是否通

刷新功能依赖 Steam Web API。在大陆机房可能时通时不通：

```bash
# 容器内测一下
docker compose exec dota-stats python -c "
import os, requests
key = os.environ.get('STEAM_API_KEY')
r = requests.get('https://api.steampowered.com/IDOTA2Match_570/GetMatchHistory/v1/',
                 params={'league_id': 19479, 'key': key}, timeout=15)
print('STATUS', r.status_code)
print(r.text[:200])
"
```

**结果分类：**

| 结果 | 含义 | 应对 |
|---|---|---|
| `STATUS 200` + 看到 matches 数据 | Steam API 通畅 | ✅ 完美，刷新按钮可用 |
| `STATUS 200` + `results` 为空 | 通畅但暂无新比赛 | ✅ 一样可用 |
| `timeout` / `Connection refused` | 大陆机房路由不通 | ⬇️ 看下方"Steam 不可达备选方案" |
| `STATUS 403` | API key 错 | 重新去 <https://steamcommunity.com/dev/apikey> |

---

## 常用运维命令

```bash
cd /root/dota-stats

# 查看运行状态
docker compose ps

# 看实时日志
docker compose logs -f

# 重启（改了 .env 后）
docker compose restart

# 停止
docker compose down

# 更新代码后重新部署
# （要么重新 scp 上传 tar，要么 git pull）
docker compose up -d --build

# 清空所有缓存重新拉
docker compose down
rm -rf data/matches/* data/aggregate.json
docker compose up -d
```

---

## 故障排查

### 浏览器一直转圈或 `ERR_CONNECTION_TIMED_OUT`
| 排查点 | 命令 |
|---|---|
| 容器是否在跑 | `docker compose ps`（State 应该是 Up） |
| 容器是否监听 8000 | `docker compose logs --tail 20`，看 `Listening at` |
| 服务器内部是否能访问 | `curl http://localhost` 在服务器上跑 |
| 80 端口防火墙 | **回第 1 步检查阿里云控制台规则** |
| 操作系统层防火墙 | `iptables -L -n` 看有没有拒绝；轻量服务器一般默认放开 |

### 容器反复重启
```bash
docker compose logs --tail 100
```
最常见是 .env 里缺字段、或 `STEAM_API_KEY` 没填。

### "还没有数据" 且后台拉取失败
这就是 Steam API 在你服务器上不通的情况。看下方备选方案。

### 内存不够（出现 `Killed` 或容器频繁重启）
轻量 2G 内存够用，1G 实例会紧张。`free -m` 查看；用 swap 兜底：
```bash
fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

---

## Steam 不可达备选方案

如果上面第 10 步发现 Steam API 在你的大陆机房不通，但你又不想换香港机房，有两种方案：

### 方案 A：本地拉，服务器只展示（最简单）
- 你本地（已经能访问 Steam）继续用 CLI 或本地网页跑全量刷新
- 把更新后的 `data/matches/*.json` 和 `data/aggregate.json` rsync/scp 到服务器
- 服务器的 Flask 直接读这些缓存展示，**完全不需要联网**

可以做成定时脚本：本地每天打完内战手动跑一次，然后一个 rsync 命令同步过去。

### 方案 B：换香港/海外机房
- 阿里云轻量 → 实例操作 → **"重置系统"** 或新购一个香港地域的服务器
- 香港机房直连 Steam 稳定，迁移整个 `/root/dota-stats/` 目录即可

### 方案 C：用 OpenDota team_members 反查（不需要 Steam）
- 编辑 `config.json` 把队员 account_id 填进 `team_members`（之前章节有详细说明）
- 重启容器
- 走的是 OpenDota 接口，国内可访问

---

## 想加什么后续操作

- 域名 + HTTPS（需要加 Caddy 反代）
- 国内备案（域名 + 大陆服务器需要 ICP 备案，免费 7-20 天；香港免备案）
- 每日自动刷新（cron 调 `/api/refresh`）
- 队员仅限白名单访问（加个简单的 HTTP Basic Auth）

任一个都告诉我，我补脚本。
