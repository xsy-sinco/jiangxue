"""把 stats.aggregate() 的结果转成前端友好的 dict（含头像 URL、衍生字段）。"""

from __future__ import annotations

from statistics import mean
from typing import Any

from src.heroes import HeroIndex
from src.stats import FactionStats, HeroStats, MatchPlayer, MatchRow, PlayerStats


def _player(p: PlayerStats, hero_index: HeroIndex) -> dict[str, Any]:
    top = [
        {
            "hero_id": hid,
            "name": hero_index.name(hid),
            "icon": hero_index.icon_url(hid),
            "picks": cnt,
            "wins": p.hero_wins.get(hid, 0),
        }
        for hid, cnt in sorted(p.hero_picks.items(), key=lambda x: -x[1])[:5]
    ]
    return {
        "account_id": p.account_id,
        "name": p.name,
        "matches": p.matches,
        "wins": p.wins,
        "losses": p.matches - p.wins,
        "winrate": round(p.winrate * 100, 1),
        "kills": p.kills,
        "deaths": p.deaths,
        "assists": p.assists,
        "kda": round(p.kda, 2),
        "avg_kills": round(p.kills / p.matches, 1) if p.matches else 0,
        "avg_deaths": round(p.deaths / p.matches, 1) if p.matches else 0,
        "avg_assists": round(p.assists / p.matches, 1) if p.matches else 0,
        "top_heroes": top,
    }


def _hero(h: HeroStats, hero_index: HeroIndex) -> dict[str, Any]:
    return {
        "hero_id": h.hero_id,
        "name": hero_index.name(h.hero_id),
        "portrait": hero_index.portrait_url(h.hero_id),
        "icon": hero_index.icon_url(h.hero_id),
        "picks": h.picks,
        "wins": h.wins,
        "losses": h.picks - h.wins,
        "winrate": round(h.winrate * 100, 1),
        "kda": round(h.kda, 2),
        "kills": h.kills,
        "deaths": h.deaths,
        "assists": h.assists,
    }


def _match_player(mp: MatchPlayer, hero_index: HeroIndex,
                  name_map: dict[int, str]) -> dict[str, Any]:
    # 统一用聚合后的最终名（玩家可能改过名 / 配置了 alias），保证
    # 「玩家排行」里看到的名字 和「对局详情」里看到的名字一致
    name = name_map.get(mp.account_id, "") if mp.account_id else ""
    if not name:
        name = mp.personaname
    return {
        "account_id": mp.account_id,
        "name": name,
        "hero_id": mp.hero_id,
        "hero_name": hero_index.name(mp.hero_id),
        "hero_icon": hero_index.icon_url(mp.hero_id),
        "is_radiant": mp.is_radiant,
        "win": mp.win,
        "kills": mp.kills,
        "deaths": mp.deaths,
        "assists": mp.assists,
        "gpm": mp.gpm,
        "xpm": mp.xpm,
        "last_hits": mp.last_hits,
        "denies": mp.denies,
        "hero_damage": mp.hero_damage,
        "tower_damage": mp.tower_damage,
        "hero_healing": mp.hero_healing,
        "net_worth": mp.net_worth,
        "level": mp.level,
    }


def _match(m: MatchRow, hero_index: HeroIndex,
           name_map: dict[int, str]) -> dict[str, Any]:
    mm, ss = divmod(m.duration_sec, 60)
    return {
        "match_id": m.match_id,
        "start_time": m.start_time.strftime("%Y-%m-%d %H:%M"),
        "start_ts": int(m.start_time.timestamp()),
        "duration": f"{mm:02d}:{ss:02d}",
        "duration_sec": m.duration_sec,
        "radiant_win": m.radiant_win,
        "winner": "radiant" if m.radiant_win else "dire",
        "radiant_score": m.radiant_score,
        "dire_score": m.dire_score,
        "radiant_players": m.radiant_players.split(", ") if m.radiant_players else [],
        "dire_players": m.dire_players.split(", ") if m.dire_players else [],
        "players": [_match_player(p, hero_index, name_map) for p in m.players],
        "dotabuff": f"https://www.dotabuff.com/matches/{m.match_id}",
        "opendota": f"https://www.opendota.com/matches/{m.match_id}",
    }


def serialize(result: dict[str, Any], hero_index: HeroIndex, league_id: int,
              league_name: str = "") -> dict[str, Any]:
    faction: FactionStats = result["faction"]
    players: dict[int, PlayerStats] = result["players"]
    heroes: dict[int, HeroStats] = result["heroes"]
    matches: list[MatchRow] = result["matches"]

    # 衍生指标
    durations = [m.duration_sec for m in matches if m.duration_sec > 0]
    avg_dur_sec = int(mean(durations)) if durations else 0
    avg_mm, avg_ss = divmod(avg_dur_sec, 60)

    total_kills = sum(m.radiant_score + m.dire_score for m in matches)
    avg_kills_per_match = round(total_kills / len(matches), 1) if matches else 0

    # 玩家最终展示名：取聚合后 PlayerStats.name（已经处理过 alias）
    name_map: dict[int, str] = {p.account_id: p.name for p in players.values()}

    return {
        "league_id": league_id,
        "league_name": league_name or f"League {league_id}",
        "summary": {
            "total_matches": len(matches),
            "radiant_wins": faction.radiant_wins,
            "dire_wins": faction.dire_wins,
            "radiant_winrate": round(faction.radiant_winrate * 100, 1),
            "dire_winrate": round((1 - faction.radiant_winrate) * 100, 1) if faction.total else 0,
            "avg_duration": f"{avg_mm:02d}:{avg_ss:02d}",
            "avg_duration_sec": avg_dur_sec,
            "active_players": len(players),
            "heroes_played": len(heroes),
            "avg_kills_per_match": avg_kills_per_match,
            "total_kills": total_kills,
        },
        "players": [_player(p, hero_index) for p in
                    sorted(players.values(), key=lambda x: -x.matches)],
        "heroes": [_hero(h, hero_index) for h in
                   sorted(heroes.values(), key=lambda x: -x.picks)],
        "matches": [_match(m, hero_index, name_map) for m in
                    sorted(matches, key=lambda x: x.start_time, reverse=True)],
    }
