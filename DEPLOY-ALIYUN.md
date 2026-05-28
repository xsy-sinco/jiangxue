# 阿里云部署指南（裸装版 · 推荐）

适用：**阿里云轻量应用服务器 SWAS** 或 **ECS**，Ubuntu 22.04 系统。

特点：不用 Docker，直接 Python + venv + systemd。**所有命令复制即可执行。**

> 想用 Docker 部署的看 [DEPLOY-ALIYUN-DOCKER.md](DEPLOY-ALIYUN-DOCKER.md)。
> 不是阿里云用户的看 [DEPLOY.md](DEPLOY.md)。

---

## 📋 部署前清单

确认你已经有：

- [ ] 服务器**公网 IP**
- [ ] **root 密码**（控制台 → 实例详情 → 重置密码）
- [ ] 阿里云控制台已**放行 80 端口**（见第 0 步）
- [ ] 本地 `E:\Sinco\project\dota-inhouse-stats\config.json` 里 `steam_api_key` 已填好
- [ ] 本地 `data/matches/` 里有约 500 个 `.json` 文件（之前跑过 `python -m src.main`）

---

## 第 0 步：阿里云控制台放行 80 端口（**最关键，漏了一切白搭**）

阿里云的"防火墙"是云端规则，必须先在控制台放行才能让外网访问。

**轻量应用服务器：**
1. 阿里云控制台 → 顶部搜 **"轻量应用服务器"**
2. 点你的实例 → 左侧 **"防火墙"** 标签
3. 点 **"添加规则"** → 应用类型选 **HTTP**（自动 TCP/80）→ 保存

**ECS：**
1. 阿里云控制台 → 顶部搜 **"云服务器 ECS"** → 实例列表 → 点你的实例
2. 左侧 **"安全组"** → 配置规则 → 入方向 → 添加 → 端口 **80**、源 `0.0.0.0/0`

> 验证：本机 PowerShell 跑 `ping <公网IP>`，能 ping 通才能继续。

---

## 第 1 步：传代码到服务器

**如果你已经传过了，跳到第 2 步。**

### 在你的 Windows 本地 PowerShell：

```powershell
cd E:\Sinco\project

# 打包（含 data/ 缓存，让首屏立刻有数据）
tar.exe `
  --exclude='dota-inhouse-stats/__pycache__' `
  --exclude='dota-inhouse-stats/.git' `
  --exclude='dota-inhouse-stats/output' `
  --exclude='dota-inhouse-stats/.venv' `
  --exclude='dota-inhouse-stats/*.log' `
  -czf dota-stats.tar.gz dota-inhouse-stats

# 看大小，含 500 场缓存约 10-30 MB
Get-Item dota-stats.tar.gz | Select-Object Length

# 上传（替换成你的 IP）
scp dota-stats.tar.gz root@<你的公网IP>:/root/
```

---

## 第 2 步：SSH 登录服务器

任选其一：

**方式 A：阿里云 Workbench**（浏览器内 SSH，最简单）
实例详情页右上 → "远程连接" → "Workbench 远程连接"

**方式 B：本地 PowerShell**
```powershell
ssh root@<你的公网IP>
# 输 yes，然后输密码
```

> 后续命令默认以 **root** 身份执行。

---

## 第 3 步：解压代码

```bash
cd /root
ls -lh dota-stats.tar.gz                # 确认包在这

tar xzf dota-stats.tar.gz
mv dota-inhouse-stats dota-stats 2>/dev/null || true
cd dota-stats
pwd                                      # 应显示 /root/dota-stats
```

验证缓存到位（**关键**：少了这步首屏就空）：

```bash
ls data/matches/ | wc -l                 # 期望 ~500
```

如果显示 `0` 或目录不存在，说明打包时排除了 `data/`，回本地重新打包（**不要用 `--exclude=data` 之类**）。

---

## 第 4 步：装 Python（一次性）

Ubuntu 22.04 默认 Python 3.10，刚好可用。

```bash
apt update
apt install -y python3 python3-venv python3-pip
python3 --version                        # 应该是 3.10.x
```

---

## 第 5 步：建虚拟环境 + 装依赖

```bash
cd /root/dota-stats

# 创建独立 venv，所有依赖隔离在 .venv/ 里，不污染系统
python3 -m venv .venv

# 激活 venv（提示符前会出现 (.venv)）
source .venv/bin/activate

# 用阿里云 pip 镜像装依赖（国内秒装）
pip install --upgrade pip
pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

# 验证
which gunicorn                           # /root/dota-stats/.venv/bin/gunicorn
gunicorn --version
```

---

## 第 6 步：写 .env 配置

```bash
cd /root/dota-stats
cp .env.example .env
nano .env
```

填入：

```
LEAGUE_ID=19479
STEAM_API_KEY=粘你那个32位的STEAM_KEY
OPENDOTA_API_KEY=
RATE_LIMIT_PER_MINUTE=55
```

保存退出：`Ctrl+O` → `Enter` → `Ctrl+X`

验证：

```bash
cat .env
# 确认 STEAM_API_KEY 是真实的 KEY，不是占位文本
```

---

## 第 7 步：手动跑一次确认能起来

```bash
cd /root/dota-stats
set -a; source .env; set +a              # 把 .env 注入到当前 shell

.venv/bin/gunicorn -w 1 --threads 4 -b 0.0.0.0:80 \
  --access-logfile - --error-logfile - --timeout 120 \
  web.app:app
```

预期输出：

```
[INFO] Listening at: http://0.0.0.0:80
[INFO] Booting worker with pid: ...
```

**新开一个 SSH / Workbench 标签**自查：

```bash
curl -I http://localhost                 # 期望：HTTP/1.1 200 OK
curl -s http://localhost/api/stats | head -c 200
```

浏览器打开 `http://<你的公网IP>`，应该看到深色的内战仪表盘 + 500 场数据。

确认完，回到跑着 gunicorn 那个窗口 **按 `Ctrl+C` 停掉**（接下来用 systemd 让它后台常驻）。

---

## 第 8 步：用 systemd 实现开机自启 + 崩了自动拉起

```bash
cat > /etc/systemd/system/dota-stats.service <<'EOF'
[Unit]
Description=Dota2 Inhouse Stats Web
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/dota-stats
EnvironmentFile=/root/dota-stats/.env
ExecStart=/root/dota-stats/.venv/bin/gunicorn -w 1 --threads 4 -b 0.0.0.0:80 --timeout 120 --access-logfile - --error-logfile - web.app:app
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
```

> ⚠️ **如果你的项目路径不是 `/root/dota-stats`**，先 `pwd` 拿到真实路径，把 unit 文件里 3 处 `/root/dota-stats` 全部替换掉，再执行上面的命令。

启用并启动：

```bash
systemctl daemon-reload
systemctl enable --now dota-stats
```

---

## 第 9 步：验证

```bash
# 服务在跑吗？
systemctl status dota-stats
# Active: active (running) 就好

# 看实时日志（Ctrl+C 退出，不影响服务）
journalctl -u dota-stats -f --no-pager

# 服务器内自查
curl -I http://localhost                 # 200 OK
ss -tlnp | grep ':80 '                   # 看到 gunicorn LISTEN 0.0.0.0:80
```

---

## 第 10 步：浏览器访问

```
http://<你的公网IP>
```

**发给队友就行**。重启服务器后会自动起，不用管。

---

## 🛠 常用运维命令

```bash
# 重启服务
systemctl restart dota-stats

# 停止 / 启动
systemctl stop dota-stats
systemctl start dota-stats

# 看状态
systemctl status dota-stats

# 看日志
journalctl -u dota-stats -f                  # 实时跟踪
journalctl -u dota-stats --since "1h ago"    # 最近 1 小时
journalctl -u dota-stats -n 100              # 最近 100 行

# 改了代码后重启生效
systemctl restart dota-stats

# 改了 .env 后也需要重启
systemctl restart dota-stats

# 升级依赖
cd /root/dota-stats
source .venv/bin/activate
pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
systemctl restart dota-stats

# 清空缓存重新拉所有比赛（一般不用）
systemctl stop dota-stats
rm -rf data/matches/* data/aggregate.json
systemctl start dota-stats
```

---

## 🩺 故障排查

| 现象 | 排查命令 / 修复 |
|---|---|
| 浏览器一直转圈打不开 | 90% 是**阿里云控制台第 0 步漏了**，回去再确认 |
| `systemctl status` 显示 failed | `journalctl -u dota-stats -n 100` 看 traceback |
| 服务起不来报 `Address already in use` | `ss -tlnp \| grep ':80 '` 看是谁占了 80；如果是手动跑剩的 gunicorn，`pkill gunicorn` 后重启 |
| ModuleNotFoundError | venv 没建好或没装全。`source .venv/bin/activate && pip install -r requirements.txt` |
| 网页打开了但永远 "还没有数据" | 后台拉取出错，`journalctl -u dota-stats -f` 看具体；常见是 STEAM_API_KEY 错或 Steam 大陆不通 |
| Steam API 报 403 | `https://steamcommunity.com/dev/apikey` 重新确认 key |

### 完整健康检查脚本（一键诊断）

```bash
echo "=== systemd status ===" && systemctl is-active dota-stats
echo "=== port listening ===" && ss -tlnp | grep ':80 '
echo "=== local HTTP ===" && curl -sI http://localhost | head -1
echo "=== app status ==="
curl -s http://localhost/api/stats | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('loading:', d['loading'])
print('error:', d.get('error'))
data = d.get('data')
if data:
    s = data['summary']
    print('league_name:', data['league_name'])
    print('matches:', s['total_matches'])
    print('players:', len(data['players']))
"
echo "=== last 10 log lines ===" && journalctl -u dota-stats -n 10 --no-pager
```

把整段贴到 SSH 里就能跑，输出贴给我就能定位问题。

---

## ⚠️ 大陆机房特有问题：Steam API 路由不稳

如果你的服务器在大陆机房（华东/华北/华南），刷新功能可能时通时不通。

**判断方式：**

```bash
source /root/dota-stats/.env

curl -s --max-time 15 \
  "https://api.steampowered.com/IDOTA2Match_570/GetMatchHistory/v1/?league_id=${LEAGUE_ID}&key=${STEAM_API_KEY}" \
  | head -c 300
```

- 看到 JSON 数据 → ✅ 通畅，刷新按钮正常用
- 长时间无响应 / `Connection timed out` → ⬇️ Steam 不可达，三个备选方案：

### 备选 A：本地拉，服务器只展示
你本地能访问 Steam，定期跑：

```powershell
# 本地刷新（带 --no-export 跳过 CSV/xlsx 生成）
cd E:\Sinco\project\dota-inhouse-stats
python -m src.main --no-export

# 把更新后的 data/ 同步到服务器（覆盖式）
scp -r data/* root@<公网IP>:/root/dota-stats/data/
ssh root@<公网IP> "systemctl restart dota-stats"
```

可以做成 PowerShell 脚本一键跑。

### 备选 B：换香港地域机房
阿里云轻量服务器可以重置成香港地域（数据不保留，需要重新部署）；或新购一台香港的，迁移整个 `/root/dota-stats/` 目录过去。

### 备选 C：切换到 OpenDota 反查通路
编辑 `/root/dota-stats/config.json`，把队员 account_id 填进 `team_members`：

```json
{
  "league_id": 19479,
  "team_members": [123456789, 987654321, ...],
  ...
}
```

然后 `systemctl restart dota-stats`。走 OpenDota 接口在大陆访问稳定。

---

## 🔒 安全建议（可选但推荐）

### 改 SSH 端口 / 用密钥登录
默认 22 端口被全网扫描，改成高位端口能挡掉 99% 的暴力破解：

```bash
sed -i 's/^#Port 22/Port 22222/' /etc/ssh/sshd_config
systemctl restart ssh
# 别忘了阿里云控制台同时放行 22222 端口
```

### 限制只允许特定 IP 访问网站
如果只想队友能看到，先让他们告诉你各自的公网 IP，然后在阿里云控制台防火墙规则里把 80 端口的源从 `0.0.0.0/0` 改成具体 IP 列表。

### HTTPS（要域名）
- 买域名（阿里云 ~¥40/年）→ DNS A 记录指向服务器 IP
- 装 Caddy 自动签 Let's Encrypt 证书 + 反代 gunicorn
- 大陆服务器 + 域名需 ICP 备案（免费 7-20 天）；香港服务器免备案

需要这块的告诉我，单独出一份指南。

---

## 📦 文件结构（部署完后）

```
/root/dota-stats/
├── .env                        # 你的密钥配置（chmod 600 保险）
├── .venv/                      # Python 虚拟环境
├── data/
│   ├── matches/*.json          # 500+ 场比赛详情缓存
│   └── aggregate.json          # 聚合后的 JSON（网页直接读）
├── src/                        # CLI 代码
├── web/                        # Flask 应用
├── requirements.txt
└── ...

/etc/systemd/system/dota-stats.service  # 系统服务定义
```

可以 `chmod 600 .env` 让 .env 仅 root 可读，多一层保险。
