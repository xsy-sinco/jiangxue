# Dota2 联赛内战数据统计

基于 **Steam Web API + OpenDota API** 的工具，输入一个 **League ID** 就能拉取该联赛下所有内战的对局数据，统计：

1. **阵营胜率** —— 天辉 vs 夜魇
2. **玩家战绩** —— 场数、胜率、KDA、常用英雄 Top5
3. **英雄统计** —— 出场、胜率、累计 KDA
4. **对局明细** —— 每场比赛的时间、时长、比分、阵容

两种使用方式：

- **🌐 网页版**（推荐）：`python serve.py` → 浏览器打开 <http://localhost:5000>
  - 深色 Dota 风仪表盘、英雄头像、阵营饼图、Top15 英雄柱状图
  - 玩家/英雄/对局三大可排序、可搜索表格
  - 一键刷新数据，实时进度条
  - **要让队友远程访问？**
    - 阿里云用户（裸装 · 推荐）→ [DEPLOY-ALIYUN.md](DEPLOY-ALIYUN.md)
    - 阿里云用户（Docker 版）→ [DEPLOY-ALIYUN-DOCKER.md](DEPLOY-ALIYUN-DOCKER.md)
    - 其他云 / 通用 → [DEPLOY.md](DEPLOY.md)
- **📟 命令行版**：`python -m src.main`
  - 终端表格摘要 + 多 sheet `.xlsx` + 分文件 `.csv` 导出

---

## 🌐 网页版速览

```powershell
cd E:\Sinco\project\dota-inhouse-stats
python serve.py
# 浏览器打开 http://localhost:5000
```

界面布局：

| 区块 | 内容 |
|---|---|
| **顶栏** | 联赛名 · 最后更新时间 · 🔄 刷新数据按钮 |
| **KPI 卡片** | 总对局、天辉胜率、平均时长、活跃玩家数、场均击杀 |
| **图表行** | 阵营胜率甜甜圈 · 英雄出场 Top15 堆叠柱状图 |
| **玩家排行** | 全部字段可点击表头排序、支持按昵称/account_id 搜索、显示每个玩家的 Top5 常用英雄头像 |
| **英雄数据** | 头像 + 数据 + 出场频次进度条；可搜可排 |
| **对局列表** | 每场对局一张卡，胜方亮、败方暗，左右两侧贴出全部英雄头像，链向 DotaBuff / OpenDota |

数据缓存在 `data/aggregate.json` 与 `data/matches/*.json`，**第二次启动秒开**，只对新比赛走网络。

---

## ⚠ 重要：数据源选择

OpenDota 把自建/小型联赛默认标为 `tier = excluded`，**OpenDota 自己的库里就根本不存这些联赛的数据**。所以光靠 OpenDota 是查不到自建联赛的（已实测验证 league 19479 在 OpenDota 库里 0 条记录）。

**真正的数据源是 Valve 的 Steam Web API**：它对任何 league_id 都返回数据，包括所有自建联赛。

本工具的工作流：

```
  Steam API GetMatchHistory?league_id=X
       ↓ 拿到 match_id 列表
  OpenDota /matches/{id}  
       ↓ 拉解析过的详细数据（玩家、英雄、KDA）
  统计 + 导出
```

发现 match_id 共支持四条路径，会自动去重合并：

| # | 路径 | 何时有效 | 配置 |
|---|---|---|---|
| 1 | **Steam `GetMatchHistory?league_id=X`** | **任何联赛 ←推荐** | `steam_api_key` |
| 2 | OpenDota `/leagues/{id}/matches` | 联赛 tier ≠ excluded 时 | 不用配 |
| 3 | OpenDota `/players/{aid}/matches?league_id=X` 队员反查 | 任何联赛 | `team_members` |
| 4 | `matches.txt` 手动指定 | 兜底 | 复制 `matches.txt.example` |

**99% 的场景只需要配 1（Steam key）就够了**。其他三条留着是为了在 Steam key 暂时拿不到时还能用。

---

## 1. 安装

需要 Python 3.9+。

```powershell
cd E:\Sinco\project\dota-inhouse-stats
pip install -r requirements.txt
```

## 2. 申请 Steam Web API Key（30 秒）

1. 打开 <https://steamcommunity.com/dev/apikey>，用你的 Steam 账号登录（账号需绑定手机和邮箱）。
2. "域名"那栏随便填，例如 `localhost` 或 `inhouse-stats`。
3. 点 Register，下方会出现一串 32 位字符，这就是你的 key。
4. 把 key 填进 `config.json` 的 `steam_api_key` 字段。

> 这是 Valve 官方接口，**完全免费**，限速宽松（约 10 万次/天）。key 不要泄露给陌生人。

## 3. 准备 League ID

1. 在 Dota2 客户端 → 联赛 → 我的联赛中创建一个联赛（免费）。
2. 内战时房主在创建大厅时勾选该联赛。
3. League ID 在 <https://www.opendota.com/leagues> 或联赛页面 URL 里能看到。

## 4. 配置文件

首次运行会自动从 `config.example.json` 拷贝 `config.json`：

```json
{
    "league_id": 19479,
    "steam_api_key": "你的32位STEAM KEY",
    "api_key": "",
    "rate_limit_per_minute": 55,
    "team_members": [],
    "player_aliases": {}
}
```

- `steam_api_key`：**核心**。Steam Web API key。
- `league_id`：你的联赛 ID。
- `api_key`：可选，OpenDota 的 key，<https://www.opendota.com/api-keys> 申请，能提升 OpenDota 拉详情的速度。
- `team_members` / `player_aliases`：可选，跑过一次后从 `output/league_XXX_csv/players.csv` 找 account_id 填别名。

## 5. 运行

### 5.1 启动网页（推荐）

```powershell
python serve.py
# 浏览器打开 http://localhost:5000
```

首次启动自动在后台拉数据，进度条会显示当前阶段；以后启动直接读缓存秒开。
点界面右上角"刷新数据"按钮可拉取新比赛（只对未缓存的 match 走网络）。

### 5.2 命令行

```powershell
# 用 config.json 里的 league_id
python -m src.main

# 临时指定
python -m src.main --league 19479

# 显示更多 Top 行
python -m src.main --top 20

# 只看摘要不导出
python -m src.main --no-export

# 强制刷新缓存
python -m src.main --refresh
```

输出文件：

```
output/
├── league_19479_stats.xlsx       # 多 sheet 工作簿（推荐）
└── league_19479_csv/
    ├── faction.csv
    ├── players.csv
    ├── heroes.csv
    └── matches.csv
```

## 6. 缓存说明

`data/matches/*.json` 缓存每场比赛的详情。比赛结束后数据不可变，第二次跑只对**新比赛**调用 OpenDota 接口。删除 `data/matches/` 即可全部重新拉取。

## 7. 常见问题

**Q: Steam API 返回 403 Forbidden**
A: `steam_api_key` 没填或不对。重新去 <https://steamcommunity.com/dev/apikey> 确认。

**Q: 我不想申请 Steam key，能不能只用 OpenDota？**
A: 联赛非 excluded 可以，留空 `steam_api_key`、跑就行。excluded 联赛只能填 `team_members` 走玩家反查，或在 `matches.txt` 手动列 match_id。

**Q: 触发 OpenDota 429 限速**
A: 调小 `rate_limit_per_minute`，或在 <https://www.opendota.com/api-keys> 申请 OpenDota key。

**Q: 玩家显示成 `id_xxxxxx`**
A: OpenDota 拿不到隐私设置的 personaname。在 `player_aliases` 里手动起别名。

**Q: 比赛打完多久能拉到？**
A: Steam 接口几乎实时；OpenDota 解析详情需要 5-15 分钟。

**Q: 匿名玩家**
A: `account_id=4294967295` 是 OpenDota 的匿名标记，在玩家统计里跳过，但仍计入阵营/英雄/对局。

## 8. 目录结构

```
dota-inhouse-stats/
├── README.md
├── requirements.txt
├── config.example.json
├── matches.txt.example
├── serve.py                # 网页启动入口
├── src/
│   ├── api.py              # OpenDota 客户端（详情 + 缓存 + 限速）
│   ├── steam_api.py        # Steam Web API 客户端（league_id 直查）
│   ├── heroes.py           # 英雄 ID → 名称 / CDN 头像
│   ├── stats.py            # 聚合计算（4 维度）
│   ├── exporter.py         # CSV / xlsx 导出
│   └── main.py             # CLI 入口 + 四路发现
├── web/
│   ├── app.py              # Flask 应用 + 路由
│   ├── serialize.py        # 数据 → 前端 JSON
│   └── templates/
│       └── index.html      # 仪表盘（Tailwind + Chart.js + Alpine）
├── data/
│   ├── matches/            # 比赛详情缓存（自动生成）
│   └── aggregate.json      # 网页用的聚合缓存
└── output/                 # CLI 导出文件
```
