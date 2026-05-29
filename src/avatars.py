"""Steam 头像解析 + 本地缓存。

每个玩家卡片默认用 Steam 头像；玩家自己上传的自定义头像（community 库里的
avatar_url）优先级更高，由前端按 自定义 > Steam > 默认 的顺序取值。

- account_id（32 位 Dota account_id）<-> steamid64 互转
- resolve(account_ids, steam_key)：有 key 就联网刷新并写缓存，没 key（如离线
  regen）就只读缓存。缓存在 data/steam_avatars.json，随 data 一起同步到服务器。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .steam_api import SteamDotaClient

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "data" / "steam_avatars.json"
# Dota account_id(32位) + 这个常量 = steamid64
ID64_BASE = 76561197960265728


def _load() -> dict[str, str]:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(cache: dict[str, str]) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def resolve(account_ids: Iterable[int], steam_key: str = "") -> dict[int, str]:
    """返回 {account_id: 头像URL}。

    有 steam_key：联网批量刷新这些玩家的头像并写缓存（取不到的保留旧缓存）。
    没 steam_key：只用现有缓存（离线 regen 场景）。
    """
    cache = _load()
    ids = [int(a) for a in account_ids if a]
    if steam_key and ids:
        try:
            client = SteamDotaClient(steam_key)
            fetched = client.get_player_summaries([a + ID64_BASE for a in ids])
            for id64, url in fetched.items():
                cache[str(id64 - ID64_BASE)] = url
            if fetched:
                _save(cache)
        except Exception:
            pass  # 联网失败就退回缓存，不影响出图
    return {a: cache[str(a)] for a in ids if str(a) in cache}


def get_one(account_id: int) -> str | None:
    return _load().get(str(account_id))
