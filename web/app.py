"""Flask 应用：内战数据查询网页。

开发：
    python serve.py                          → http://localhost:5000
生产：
    gunicorn -w 1 --threads 4 -b 0.0.0.0:8000 web.app:app
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template, request

from src.api import OpenDotaClient
from src.heroes import HeroIndex
from src.serialize import serialize
from src.stats import aggregate
from src.steam_api import SteamDotaClient

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
CACHE_JSON = ROOT / "data" / "aggregate.json"

app = Flask(__name__, template_folder="templates", static_folder="static")

# Session 签名密钥。生产环境必须设 FLASK_SECRET_KEY 环境变量。
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-in-prod-please")

# 信任反代的 X-Forwarded-Proto 等头，让 url_for(_external=True) 生成 https
try:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
except ImportError:
    pass

# gzip 压缩响应（aggregate.json 2.4MB → ~400KB，渲染快很多）
try:
    from flask_compress import Compress
    Compress(app)
except ImportError:
    pass  # 服务器没装就跳过，不致命

# 注册社区蓝图
from web import auth, community_api, db  # noqa: E402
app.register_blueprint(auth.bp)
app.register_blueprint(community_api.bp)
db.init_db()

# ---------- 状态：进度 + 数据 ----------

_state_lock = threading.Lock()
_state = {
    "loading": False,
    "progress": {"phase": "idle", "current": 0, "total": 0, "message": ""},
    "data": None,        # 序列化后的 dict
    "updated_at": None,  # epoch
    "error": None,
}


def _set_progress(phase: str, current: int = 0, total: int = 0, message: str = "") -> None:
    with _state_lock:
        _state["progress"] = {
            "phase": phase, "current": current, "total": total, "message": message
        }


def _load_config() -> dict:
    """配置加载顺序：config.json 为基础，环境变量覆盖。

    生产部署时不应该把 config.json 推到镜像/仓库里，全部走 env：
      LEAGUE_ID, STEAM_API_KEY, OPENDOTA_API_KEY, RATE_LIMIT_PER_MINUTE
    """
    cfg: dict = {}
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if os.environ.get("LEAGUE_ID"):
        cfg["league_id"] = int(os.environ["LEAGUE_ID"])
    if os.environ.get("STEAM_API_KEY"):
        cfg["steam_api_key"] = os.environ["STEAM_API_KEY"]
    if os.environ.get("OPENDOTA_API_KEY"):
        cfg["api_key"] = os.environ["OPENDOTA_API_KEY"]
    if os.environ.get("RATE_LIMIT_PER_MINUTE"):
        cfg["rate_limit_per_minute"] = int(os.environ["RATE_LIMIT_PER_MINUTE"])
    return cfg


def _get_league_name(league_id: int) -> str:
    """从 OpenDota 拿联赛元数据的 name 字段（即使 tier=excluded 也有）。"""
    try:
        r = requests.get(f"https://api.opendota.com/api/leagues/{league_id}", timeout=10)
        if r.ok:
            return r.json().get("name", "") or ""
    except Exception:
        pass
    return ""


def _do_refresh() -> None:
    """后台跑：发现 → 拉详情 → 聚合 → 序列化。把结果写入 _state。"""
    try:
        cfg = _load_config()
        league_id = cfg.get("league_id")
        if not league_id:
            raise RuntimeError("config.json 缺少 league_id")

        cache_dir = ROOT / cfg.get("cache_dir", "data/matches")
        client = OpenDotaClient(
            cache_dir=cache_dir,
            api_key=cfg.get("api_key", ""),
            rate_limit_per_minute=cfg.get("rate_limit_per_minute", 55),
        )

        _set_progress("heroes", message="加载英雄常量")
        hero_index = HeroIndex(client.get_heroes())

        _set_progress("league", message="查询联赛名称")
        league_name = _get_league_name(league_id)

        _set_progress("discover", message="通过 Steam 发现比赛 ID")
        steam_key = cfg.get("steam_api_key", "").strip()
        match_ids: set[int] = set()
        if steam_key:
            steam = SteamDotaClient(steam_key)
            for mid in steam.get_match_ids_by_league(league_id):
                match_ids.add(mid)
        # 也叠加 OpenDota 接口和 team_members 反查（容错）
        try:
            for m in client.get_league_matches(league_id):
                if m.get("match_id"):
                    match_ids.add(m["match_id"])
        except Exception:
            pass
        for aid in cfg.get("team_members", []):
            try:
                for m in client.get_player_matches(int(aid), league_id=league_id):
                    if m.get("match_id"):
                        match_ids.add(m["match_id"])
            except Exception:
                pass

        sorted_ids = sorted(match_ids, reverse=True)
        total = len(sorted_ids)
        details = []
        for i, mid in enumerate(sorted_ids, 1):
            _set_progress("details", current=i, total=total,
                          message=f"拉取比赛详情 {i}/{total}")
            details.append(client.get_match(mid))

        _set_progress("aggregate", message="聚合统计")
        result = aggregate(details, hero_index, cfg.get("player_aliases"), league_id=league_id)
        data = serialize(result, hero_index, league_id, league_name)

        # 写盘缓存
        CACHE_JSON.parent.mkdir(parents=True, exist_ok=True)
        CACHE_JSON.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

        with _state_lock:
            _state["data"] = data
            _state["updated_at"] = int(time.time())
            _state["error"] = None
        _set_progress("done", current=total, total=total, message="完成")
    except Exception as e:
        with _state_lock:
            _state["error"] = str(e)
        _set_progress("error", message=str(e))
    finally:
        with _state_lock:
            _state["loading"] = False


def _load_cached() -> None:
    if CACHE_JSON.exists():
        try:
            data = json.loads(CACHE_JSON.read_text(encoding="utf-8"))
            with _state_lock:
                _state["data"] = data
                _state["updated_at"] = int(CACHE_JSON.stat().st_mtime)
        except Exception:
            pass


# ---------- 路由 ----------


@app.route("/")
def home():
    return render_template("home.html", active="home")


@app.route("/stats")
def stats_page():
    cfg = _load_config()
    return render_template("index.html", league_id=cfg.get("league_id"), active="stats")


@app.route("/me")
def me_page():
    aid, steam = auth.current_user()
    return render_template("me.html", active="me", account_id=aid, steam_id=steam)


@app.route("/player/<int:account_id>")
def player_page(account_id: int):
    return render_template("player.html", active="home", account_id=account_id)


@app.route("/events")
def events_page():
    return render_template("events.html", active="events")


@app.route("/api/stats")
def api_stats():
    with _state_lock:
        return jsonify({
            "data": _state["data"],
            "updated_at": _state["updated_at"],
            "loading": _state["loading"],
            "error": _state["error"],
        })


@app.route("/api/progress")
def api_progress():
    with _state_lock:
        return jsonify({
            "loading": _state["loading"],
            "progress": _state["progress"],
            "error": _state["error"],
        })


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    with _state_lock:
        if _state["loading"]:
            return jsonify({"ok": False, "message": "已有刷新任务在跑"}), 409
        _state["loading"] = True
        _state["error"] = None
    threading.Thread(target=_do_refresh, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/reload-cache", methods=["POST"])
def api_reload_cache():
    """重读 data/aggregate.json 进内存 —— 配合本地 sync.bat 推完数据后用。
    不联网，不调 Steam/OpenDota，纯磁盘 → 内存替换。"""
    if not CACHE_JSON.exists():
        return jsonify({"ok": False, "message": "data/aggregate.json 不存在"}), 404
    try:
        data = json.loads(CACHE_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({"ok": False, "message": f"解析失败: {e}"}), 500
    with _state_lock:
        _state["data"] = data
        _state["updated_at"] = int(CACHE_JSON.stat().st_mtime)
        _state["error"] = None
    return jsonify({
        "ok": True,
        "matches": len(data.get("matches", [])),
        "updated_at": _state["updated_at"],
    })


# ---------- 启动 ----------


_init_lock = threading.Lock()
_initialized = False


def init_app() -> None:
    """加载缓存 + 必要时触发首次刷新。幂等。开发服 / gunicorn 都能调。"""
    global _initialized
    with _init_lock:
        if _initialized:
            return
        _initialized = True
    _load_cached()
    if _state["data"] is None:
        with _state_lock:
            _state["loading"] = True
        threading.Thread(target=_do_refresh, daemon=True).start()


def main() -> None:
    # Windows 默认 GBK 控制台无法编码 emoji / → 等字符，会让启动 print 崩溃
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    init_app()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    print(f"\n🎮 内战数据网页已启动")
    print(f"   浏览器打开 → http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False)


# gunicorn 直接导入 `web.app:app` 时，模块加载即触发 init（生产入口）
if os.environ.get("WSGI_AUTOINIT", "0") == "1":
    init_app()


if __name__ == "__main__":
    main()
