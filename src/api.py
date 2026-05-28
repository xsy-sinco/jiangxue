"""OpenDota API 客户端：带速率限制与本地缓存。

OpenDota 免费档：60 req/min、2000 req/day；带 api_key 后限制更宽松。
比赛结束后数据是不可变的，所以详情可以永久缓存到磁盘。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://api.opendota.com/api"
ANONYMOUS_ACCOUNT_ID = 4294967295  # OpenDota 用此值表示匿名玩家


class OpenDotaClient:
    def __init__(
        self,
        cache_dir: str | Path,
        api_key: str = "",
        rate_limit_per_minute: int = 55,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key.strip()
        # 留 5 次余量避免触发限速；min_interval 是相邻请求最小间隔（秒）
        self.min_interval = 60.0 / max(rate_limit_per_minute, 1)
        self._last_request_ts = 0.0
        self.session = requests.Session()

    # ---------- 底层 HTTP ----------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        params = dict(params or {})
        if self.api_key:
            params["api_key"] = self.api_key

        # 简单速率限制
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

        url = f"{BASE_URL}{path}"
        for attempt in range(3):
            resp = self.session.get(url, params=params, timeout=30)
            self._last_request_ts = time.monotonic()
            if resp.status_code == 429:
                # 被限速，指数退避
                wait = 5 * (attempt + 1)
                print(f"  [限速] 429，等待 {wait}s 后重试…")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"请求失败（重试 3 次）：{url}")

    # ---------- 业务接口 ----------

    def get_league_matches(self, league_id: int) -> list[dict[str, Any]]:
        """拉取某联赛下所有比赛摘要（不含选手详情）。"""
        return self._get(f"/leagues/{league_id}/matches")

    def get_match(self, match_id: int, *, force: bool = False) -> dict[str, Any]:
        """获取比赛详情，结果永久缓存到 cache_dir/<match_id>.json。"""
        cache_file = self.cache_dir / f"{match_id}.json"
        if cache_file.exists() and not force:
            with cache_file.open("r", encoding="utf-8") as f:
                return json.load(f)
        data = self._get(f"/matches/{match_id}")
        with cache_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return data

    def get_player_matches(
        self,
        account_id: int,
        league_id: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """拉取某玩家的比赛列表。

        关键：OpenDota 该接口支持 ?league_id=X 过滤，所以即使联赛 tier=excluded
        无法通过 /leagues/{id}/matches 查到，也能从队员视角反查到联赛内所有比赛。
        """
        params: dict[str, Any] = {}
        if league_id is not None:
            params["league_id"] = league_id
        if limit is not None:
            params["limit"] = limit
        return self._get(f"/players/{account_id}/matches", params=params)

    def get_heroes(self) -> list[dict[str, Any]]:
        """英雄常量表（id → 本地化名等）。缓存在 cache_dir/_heroes.json。"""
        cache_file = self.cache_dir / "_heroes.json"
        if cache_file.exists():
            with cache_file.open("r", encoding="utf-8") as f:
                return json.load(f)
        data = self._get("/heroes")
        with cache_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return data
