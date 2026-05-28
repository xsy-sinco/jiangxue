"""Steam Web API 客户端：直接按 league_id 查所有比赛。

为什么单独抽出来：OpenDota 对 tier=excluded 联赛不收录，但 Steam 自己的
GetMatchHistory 接口对任何 league_id 都能返回数据。这是自建/小型联赛的唯一直查通路。

申请 key：https://steamcommunity.com/dev/apikey （免费、即时）
"""

from __future__ import annotations

import time

import requests

BASE_URL = "https://api.steampowered.com/IDOTA2Match_570"


class SteamDotaClient:
    def __init__(self, api_key: str, min_interval: float = 1.0) -> None:
        if not api_key:
            raise ValueError("Steam API key 为空。访问 https://steamcommunity.com/dev/apikey 申请。")
        self.api_key = api_key.strip()
        self.min_interval = min_interval
        self._last_ts = 0.0
        self.session = requests.Session()

    def _get(self, path: str, params: dict) -> dict:
        elapsed = time.monotonic() - self._last_ts
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        params = {**params, "key": self.api_key}
        for attempt in range(3):
            resp = self.session.get(f"{BASE_URL}{path}", params=params, timeout=30)
            self._last_ts = time.monotonic()
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"  [Steam 限速] 429，等待 {wait}s 重试…")
                time.sleep(wait)
                continue
            if resp.status_code == 403:
                raise RuntimeError(
                    "Steam API 返回 403：steam_api_key 无效或未授权。"
                    "请到 https://steamcommunity.com/dev/apikey 确认 key 正确。"
                )
            resp.raise_for_status()
            return resp.json().get("result", {})
        raise RuntimeError(f"Steam 请求失败（重试 3 次）：{path}")

    def get_match_ids_by_league(self, league_id: int, max_pages: int = 50) -> list[int]:
        """分页拉取一个 league 下所有 match_id（最新→最旧）。

        Steam GetMatchHistory 每次最多 100 条；用 start_at_match_id 翻页。
        max_pages 给一个保险上限（默认 50 → 最多 5000 场，对内战足够）。
        """
        match_ids: list[int] = []
        start_at: int | None = None
        for page in range(max_pages):
            params: dict = {"league_id": league_id, "matches_requested": 100}
            if start_at is not None:
                params["start_at_match_id"] = start_at
            result = self._get("/GetMatchHistory/v1/", params)
            matches = result.get("matches", [])
            if not matches:
                break
            for m in matches:
                mid = m.get("match_id")
                if mid:
                    match_ids.append(mid)
            results_remaining = result.get("results_remaining", 0)
            if results_remaining <= 0:
                break
            # Steam 翻页约定：下一页 start_at_match_id = 本页最后一场 match_id - 1
            start_at = matches[-1]["match_id"] - 1
        return match_ids
