"""英雄 ID → 名称映射封装。"""

from __future__ import annotations

from typing import Any


class HeroIndex:
    def __init__(self, heroes: list[dict[str, Any]]) -> None:
        # OpenDota /heroes 返回 [{id, name, localized_name, ...}, ...]
        self._by_id: dict[int, dict[str, Any]] = {h["id"]: h for h in heroes}

    def name(self, hero_id: int) -> str:
        h = self._by_id.get(hero_id)
        if not h:
            return f"hero_{hero_id}"
        # localized_name 是英文常用名，例如 "Anti-Mage"
        return h.get("localized_name") or h.get("name", f"hero_{hero_id}")

    def slug(self, hero_id: int) -> str:
        """英雄内部 slug，例如 'antimage'，用来拼头像 URL。"""
        h = self._by_id.get(hero_id)
        if not h:
            return ""
        raw = h.get("name", "")
        return raw.removeprefix("npc_dota_hero_")

    def portrait_url(self, hero_id: int) -> str:
        """OpenDota CDN 头像 URL（横向 256x144 那张）。"""
        slug = self.slug(hero_id)
        if not slug:
            return ""
        return f"https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/{slug}.png"

    def icon_url(self, hero_id: int) -> str:
        """方形小图标 URL，适合表格里用。"""
        slug = self.slug(hero_id)
        if not slug:
            return ""
        return f"https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/icons/{slug}.png"

    def __len__(self) -> int:
        return len(self._by_id)
