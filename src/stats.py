"""从原始比赛详情计算四类统计：阵营 / 玩家 / 英雄 / 对局列表。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from .api import ANONYMOUS_ACCOUNT_ID
from .heroes import HeroIndex

# 幽灵对局阈值：双方总人头（radiant_score + dire_score）低于此值视为开局即退的废局，直接丢弃
GHOST_MIN_TOTAL_KILLS = 6


def _is_radiant(player: dict[str, Any]) -> bool:
    # 优先用 OpenDota 提供的字段，否则按 player_slot 高位判断
    if "isRadiant" in player:
        return bool(player["isRadiant"])
    return (player.get("player_slot", 0) & 0x80) == 0


# ---------- 数据结构 ----------


@dataclass
class FactionStats:
    radiant_wins: int = 0
    dire_wins: int = 0

    @property
    def total(self) -> int:
        return self.radiant_wins + self.dire_wins

    @property
    def radiant_winrate(self) -> float:
        return self.radiant_wins / self.total if self.total else 0.0


@dataclass
class PlayerStats:
    account_id: int
    name: str = ""
    matches: int = 0
    wins: int = 0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    # hero_id → 场数
    hero_picks: dict[int, int] = field(default_factory=dict)
    # hero_id → 胜场
    hero_wins: dict[int, int] = field(default_factory=dict)

    @property
    def winrate(self) -> float:
        return self.wins / self.matches if self.matches else 0.0

    @property
    def kda(self) -> float:
        # (K+A)/max(D,1)，标准 KDA 公式
        return (self.kills + self.assists) / max(self.deaths, 1)

    def top_heroes(self, hero_index: HeroIndex, n: int = 3) -> list[tuple[str, int, int]]:
        """返回 [(英雄名, 场数, 胜场)]，按场数倒序。"""
        items = sorted(self.hero_picks.items(), key=lambda x: -x[1])[:n]
        return [
            (hero_index.name(hid), cnt, self.hero_wins.get(hid, 0))
            for hid, cnt in items
        ]


@dataclass
class HeroStats:
    hero_id: int
    picks: int = 0
    wins: int = 0
    kills: int = 0
    deaths: int = 0
    assists: int = 0

    @property
    def winrate(self) -> float:
        return self.wins / self.picks if self.picks else 0.0

    @property
    def kda(self) -> float:
        # 累计 (K+A)/max(D,1)
        if not self.picks:
            return 0.0
        return (self.kills + self.assists) / max(self.deaths, 1)


@dataclass
class MatchPlayer:
    """单场比赛里某个玩家的核心数据，给 MVP / 最C / 躺赢 等评比用。"""
    account_id: int | None         # 匿名玩家是 None
    personaname: str               # 显示名（OpenDota 给的，玩家可能自己改过）
    hero_id: int
    is_radiant: bool
    win: bool
    kills: int
    deaths: int
    assists: int
    gpm: int                       # gold_per_min
    xpm: int                       # xp_per_min
    last_hits: int
    denies: int
    hero_damage: int
    tower_damage: int
    hero_healing: int
    net_worth: int
    level: int


@dataclass
class MatchRow:
    match_id: int
    start_time: datetime
    duration_sec: int
    radiant_win: bool
    radiant_score: int
    dire_score: int
    radiant_players: str  # 逗号拼接的英雄名
    dire_players: str
    players: list[MatchPlayer] = field(default_factory=list)


# ---------- 聚合主函数 ----------


def aggregate(
    matches: Iterable[dict[str, Any]],
    hero_index: HeroIndex,
    player_aliases: dict[str, str] | None = None,
    league_id: int | None = None,
) -> dict[str, Any]:
    """输入比赛详情迭代器，返回包含四类统计的字典。

    league_id: 可选。如果传入，只处理 m['leagueid'] == league_id 的比赛，
    用于防止玩家反查或缓存里混进其他联赛的比赛。
    """
    aliases = player_aliases or {}

    faction = FactionStats()
    players: dict[int, PlayerStats] = {}
    heroes: dict[int, HeroStats] = {}
    match_rows: list[MatchRow] = []

    # 按时间升序处理，保证 PlayerStats.name 最终是「最近一场的 personaname」
    # （玩家改过名时，这是合理的「当前名」）
    matches = sorted(matches, key=lambda x: x.get("start_time", 0))

    # 客户端兜底：剔除非目标联赛的比赛（防 OpenDota / 缓存 / matches.txt 串污）
    skipped_league = 0
    if league_id is not None:
        before = len(matches)
        matches = [m for m in matches if m.get("leagueid") == league_id]
        skipped_league = before - len(matches)
        if skipped_league:
            print(f"  [过滤] 跳过 {skipped_league} 场非 league={league_id} 的比赛")

    # 剔除"幽灵对局"：开局后玩家基本都退了的局。没有干净的字段能判定
    # （实测 leaver_status 全员退出=0 场），用总人头兜底：双方总击杀 < 阈值即丢弃。
    # 这类对局完全不计入统计（总对局/玩家/英雄/对局列表都不出现）。
    before = len(matches)
    matches = [
        m for m in matches
        if (m.get("radiant_score", 0) or 0) + (m.get("dire_score", 0) or 0) >= GHOST_MIN_TOTAL_KILLS
    ]
    skipped_ghost = before - len(matches)
    if skipped_ghost:
        print(f"  [过滤] 跳过 {skipped_ghost} 场幽灵对局（双方总人头 < {GHOST_MIN_TOTAL_KILLS}）")

    for m in matches:
        radiant_win = bool(m.get("radiant_win"))
        if radiant_win:
            faction.radiant_wins += 1
        else:
            faction.dire_wins += 1

        radiant_heroes: list[str] = []
        dire_heroes: list[str] = []
        match_players: list[MatchPlayer] = []

        for p in m.get("players", []):
            hero_id = p.get("hero_id", 0)
            # 跳过没选英雄的（断线 / 重连失败 / OpenDota 数据缺失），
            # 否则会聚合出一个 "hero_0" 幽灵英雄
            if not hero_id:
                continue
            is_radiant = _is_radiant(p)
            won = is_radiant == radiant_win
            kills = p.get("kills", 0) or 0
            deaths = p.get("deaths", 0) or 0
            assists = p.get("assists", 0) or 0
            hero_name = hero_index.name(hero_id)

            if is_radiant:
                radiant_heroes.append(hero_name)
            else:
                dire_heroes.append(hero_name)

            account_id_raw = p.get("account_id")
            display_account_id = (
                None if account_id_raw is None or account_id_raw == ANONYMOUS_ACCOUNT_ID
                else account_id_raw
            )
            display_name = (
                aliases.get(str(display_account_id)) if display_account_id is not None else None
            ) or p.get("personaname") or (
                f"id_{display_account_id}" if display_account_id is not None else "匿名玩家"
            )
            match_players.append(MatchPlayer(
                account_id=display_account_id,
                personaname=display_name,
                hero_id=hero_id,
                is_radiant=is_radiant,
                win=won,
                kills=kills,
                deaths=deaths,
                assists=assists,
                gpm=p.get("gold_per_min", 0) or 0,
                xpm=p.get("xp_per_min", 0) or 0,
                last_hits=p.get("last_hits", 0) or 0,
                denies=p.get("denies", 0) or 0,
                hero_damage=p.get("hero_damage", 0) or 0,
                tower_damage=p.get("tower_damage", 0) or 0,
                hero_healing=p.get("hero_healing", 0) or 0,
                net_worth=p.get("net_worth", 0) or 0,
                level=p.get("level", 0) or 0,
            ))

            # 英雄维度
            hs = heroes.setdefault(hero_id, HeroStats(hero_id=hero_id))
            hs.picks += 1
            if won:
                hs.wins += 1
            hs.kills += kills
            hs.deaths += deaths
            hs.assists += assists

            # 玩家维度（跳过匿名）
            account_id = p.get("account_id")
            if account_id is None or account_id == ANONYMOUS_ACCOUNT_ID:
                continue
            ps = players.setdefault(account_id, PlayerStats(account_id=account_id))
            # 名称优先用配置别名，其次 personaname
            ps.name = aliases.get(str(account_id)) or p.get("personaname") or ps.name or f"id_{account_id}"
            ps.matches += 1
            if won:
                ps.wins += 1
            ps.kills += kills
            ps.deaths += deaths
            ps.assists += assists
            ps.hero_picks[hero_id] = ps.hero_picks.get(hero_id, 0) + 1
            if won:
                ps.hero_wins[hero_id] = ps.hero_wins.get(hero_id, 0) + 1

        match_rows.append(
            MatchRow(
                match_id=m.get("match_id", 0),
                start_time=datetime.fromtimestamp(m.get("start_time", 0)),
                duration_sec=m.get("duration", 0) or 0,
                radiant_win=radiant_win,
                radiant_score=m.get("radiant_score", 0) or 0,
                dire_score=m.get("dire_score", 0) or 0,
                radiant_players=", ".join(radiant_heroes),
                dire_players=", ".join(dire_heroes),
                players=match_players,
            )
        )

    return {
        "faction": faction,
        "players": players,
        "heroes": heroes,
        "matches": match_rows,
    }
