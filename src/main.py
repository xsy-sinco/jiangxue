"""CLI 入口：python -m src.main --league 19479"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tabulate import tabulate

from . import avatars
from .api import OpenDotaClient
from .exporter import export_all
from .heroes import HeroIndex
from .serialize import serialize
from .stats import aggregate
from .steam_api import SteamDotaClient

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.json"
EXAMPLE_CONFIG = ROOT / "config.example.json"
MATCHES_TXT = ROOT / "matches.txt"


def load_config(path: Path) -> dict:
    if not path.exists():
        if EXAMPLE_CONFIG.exists():
            print(f"未找到 {path.name}，正在从 config.example.json 拷贝…")
            path.write_text(EXAMPLE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            return {}
    return json.loads(path.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="基于 OpenDota API 的 Dota2 联赛内战数据统计工具",
    )
    p.add_argument("--league", "-l", type=int, help="联赛 League ID（覆盖配置文件）")
    p.add_argument("--config", "-c", type=Path, default=DEFAULT_CONFIG, help="配置文件路径")
    p.add_argument("--refresh", action="store_true", help="忽略缓存重新拉取所有比赛详情")
    p.add_argument("--top", type=int, default=10, help="终端摘要显示的 Top N 行（默认 10）")
    p.add_argument("--no-export", action="store_true", help="只在终端显示，不导出文件")
    return p.parse_args()


# ---------- 发现 match_id ----------


def read_manual_match_ids(path: Path) -> list[int]:
    """从 matches.txt 读手动指定的 match_id。每行一个，# 起头视为注释，空行忽略。"""
    if not path.exists():
        return []
    ids: list[int] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            ids.append(int(line))
        except ValueError:
            print(f"  [警告] matches.txt 跳过非法行：{raw!r}")
    return ids


def discover_match_ids(
    client: OpenDotaClient,
    steam_client: SteamDotaClient | None,
    league_id: int,
    team_members: list[int],
) -> tuple[set[int], dict[str, int]]:
    """四条路径发现内战 match_id，返回 (id 集合, 来源计数)。

    优先级（推荐用 1 就够了）：
    1. Steam GetMatchHistory?league_id=X —— 直查任何联赛（含 excluded），需 Steam key
    2. OpenDota /leagues/{id}/matches    —— 仅 tier ≠ excluded 时有效
    3. OpenDota /players/{aid}/matches?league_id=X —— 队员反查兜底
    4. matches.txt                       —— 手动兜底
    """
    found: set[int] = set()
    counters = {"Steam直查": 0, "OpenDota联赛": 0, "玩家反查": 0, "matches.txt": 0}

    # 1) Steam 直查（推荐）
    if steam_client is not None:
        try:
            print(f"  [1/4] Steam GetMatchHistory 直查 league={league_id}…")
            ids = steam_client.get_match_ids_by_league(league_id)
            for mid in ids:
                found.add(mid)
                counters["Steam直查"] += 1
            print(f"        Steam 返回 {len(ids)} 场")
        except Exception as e:
            print(f"  [警告] Steam 直查失败：{e}")
    else:
        print(f"  [1/4] 未配置 steam_api_key，跳过 Steam 直查")

    # 2) OpenDota 联赛接口
    try:
        rows = client.get_league_matches(league_id)
        new = 0
        for m in rows:
            mid = m.get("match_id")
            if mid:
                if mid not in found:
                    new += 1
                found.add(mid)
                counters["OpenDota联赛"] += 1
        print(f"  [2/4] OpenDota 联赛接口返回 {len(rows)} 场，新增 {new} 场")
    except Exception as e:
        print(f"  [2/4] OpenDota 联赛接口失败（excluded 联赛预期会空）：{e}")

    # 3) 玩家反查
    # 注意：OpenDota 的 ?league_id= 在 /players/X/matches 上不能精确过滤
    # （只对 OpenDota 索引过 leagueid 的记录生效），实测会把玩家的全部生涯
    # 比赛大量返回。所以这里加 limit=300（取最近 300 场），把每个玩家的
    # 候选数量封顶，避免炸出几万场不相关的比赛去抢 OpenDota 配额。
    # 后续在 aggregate() 里按 leagueid 做最终过滤兜底。
    PER_PLAYER_LIMIT = 300
    if team_members:
        print(f"  [3/4] 通过 {len(team_members)} 名队员反查（每人限 {PER_PLAYER_LIMIT} 场最近）…")
        for aid in team_members:
            try:
                rows = client.get_player_matches(aid, league_id=league_id, limit=PER_PLAYER_LIMIT)
            except Exception as e:
                print(f"        [警告] 玩家 {aid} 反查失败：{e}")
                continue
            new = 0
            for m in rows:
                mid = m.get("match_id")
                if mid and mid not in found:
                    found.add(mid)
                    new += 1
                if mid:
                    counters["玩家反查"] += 1
            print(f"        玩家 {aid}: 返回 {len(rows)} 场，新增 {new} 场")
    else:
        print(f"  [3/4] 未配置 team_members，跳过玩家反查")

    # 4) 手动 matches.txt
    manual = read_manual_match_ids(MATCHES_TXT)
    if manual:
        new_manual = sum(1 for mid in manual if mid not in found)
        for mid in manual:
            found.add(mid)
            counters["matches.txt"] += 1
        print(f"  [4/5] matches.txt 共 {len(manual)} 个 ID，新增 {new_manual} 场")
    else:
        print(f"  [4/5] 无 matches.txt 或为空，跳过")

    # 5) 本地缓存里属于本联赛的比赛（保证历史不丢）
    # 网络源（Steam/OpenDota）只看到最近 ~500 场，但缓存里可能还有更老的同联赛比赛。
    # 这里把它们也并入，让 aggregate 看到全部历史。
    counters["缓存"] = 0
    try:
        cache_dir = client.cache_dir
        added_from_cache = 0
        for f in cache_dir.glob("*.json"):
            if f.name.startswith("_"):
                continue
            try:
                mid = int(f.stem)
            except ValueError:
                continue
            if mid in found:
                continue
            try:
                with f.open("r", encoding="utf-8") as fh:
                    m = json.load(fh)
            except Exception:
                continue
            if m.get("leagueid") == league_id:
                found.add(mid)
                added_from_cache += 1
        counters["缓存"] = added_from_cache
        print(f"  [5/5] 本地缓存里属于 league={league_id} 的：新增 {added_from_cache} 场")
    except Exception as e:
        print(f"  [5/5] 缓存扫描失败：{e}")

    return found, counters


# ---------- 终端摘要 ----------


def print_summary(result: dict, hero_index: HeroIndex, top_n: int) -> None:
    faction = result["faction"]
    players = result["players"]
    heroes = result["heroes"]
    matches = result["matches"]

    print()
    print(f"=== 总览 ===")
    print(f"对局数：{len(matches)}    天辉胜：{faction.radiant_wins}    "
          f"夜魇胜：{faction.dire_wins}    天辉胜率：{faction.radiant_winrate * 100:.1f}%")

    print()
    print(f"=== 玩家战绩 Top {top_n}（按场数）===")
    rows = []
    for p in sorted(players.values(), key=lambda x: -x.matches)[:top_n]:
        top = p.top_heroes(hero_index, 3)
        top_str = ", ".join(f"{n}({c})" for n, c, _ in top)
        rows.append([
            p.name, p.matches, p.wins, f"{p.winrate * 100:.1f}%",
            p.kills, p.deaths, p.assists, f"{p.kda:.2f}", top_str,
        ])
    print(tabulate(
        rows,
        headers=["玩家", "场", "胜", "胜率", "K", "D", "A", "KDA", "常用英雄"],
        tablefmt="github",
    ))

    print()
    print(f"=== 英雄统计 Top {top_n}（按出场）===")
    rows = []
    for h in sorted(heroes.values(), key=lambda x: -x.picks)[:top_n]:
        rows.append([
            hero_index.name(h.hero_id), h.picks, h.wins,
            f"{h.winrate * 100:.1f}%", f"{h.kda:.2f}",
        ])
    print(tabulate(
        rows,
        headers=["英雄", "出场", "胜场", "胜率", "累计KDA"],
        tablefmt="github",
    ))


# ---------- 主流程 ----------


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)

    league_id = args.league or cfg.get("league_id")
    if not league_id:
        print("错误：未指定 league_id，请用 --league 或在 config.json 中配置。", file=sys.stderr)
        return 2

    team_members = [int(x) for x in cfg.get("team_members", [])]
    steam_key = cfg.get("steam_api_key", "").strip()

    cache_dir = ROOT / cfg.get("cache_dir", "data/matches")
    output_dir = ROOT / cfg.get("output_dir", "output")
    client = OpenDotaClient(
        cache_dir=cache_dir,
        api_key=cfg.get("api_key", ""),
        rate_limit_per_minute=cfg.get("rate_limit_per_minute", 55),
    )
    steam_client = SteamDotaClient(steam_key) if steam_key else None

    print(f"加载英雄常量…")
    hero_index = HeroIndex(client.get_heroes())
    print(f"  已加载 {len(hero_index)} 个英雄")

    print(f"发现 league={league_id} 的比赛 ID…")
    match_ids, counters = discover_match_ids(client, steam_client, league_id, team_members)

    print(f"  来源统计：Steam={counters['Steam直查']}  "
          f"OpenDota联赛={counters['OpenDota联赛']}  "
          f"玩家反查={counters['玩家反查']}  matches.txt={counters['matches.txt']}  "
          f"缓存={counters.get('缓存', 0)}")
    print(f"  去重后共 {len(match_ids)} 场比赛")

    if not match_ids:
        print()
        print("找不到任何比赛。可能原因：")
        print("  - 联赛 tier=excluded 且未配 steam_api_key（推荐配，一劳永逸）")
        print("  - Steam key 填了但联赛下确实还没打过比赛")
        print("  - 联赛 ID 写错")
        return 1

    print(f"逐场获取详情（缓存命中将跳过网络）…")
    details = []
    total = len(match_ids)
    sorted_ids = sorted(match_ids, reverse=True)  # 新的先拉
    for i, mid in enumerate(sorted_ids, 1):
        print(f"  [{i}/{total}] match_id={mid}", end="\r")
        details.append(client.get_match(mid, force=args.refresh))
    print()

    print("计算统计…")
    result = aggregate(details, hero_index, cfg.get("player_aliases"), league_id=league_id)

    # 拉取玩家 Steam 头像（默认头像；自定义头像由前端覆盖）
    steam_avatars = avatars.resolve(list(result["players"].keys()), steam_key)

    # 同步生成网页用的 aggregate.json（即使 --no-export 也写，因为体积小且网页要用）
    agg_path = ROOT / "data" / "aggregate.json"
    agg_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = serialize(result, hero_index, league_id, league_name="",
                           steam_avatars=steam_avatars)
    agg_path.write_text(json.dumps(serialized, ensure_ascii=False), encoding="utf-8")
    print(f"  已更新 {agg_path.relative_to(ROOT)} (网页读这个)")

    print_summary(result, hero_index, args.top)

    if not args.no_export:
        print()
        print("导出文件…")
        xlsx = export_all(result, hero_index, output_dir, league_id)
        print(f"  已写入 {xlsx}")
        print(f"  CSV 在    {xlsx.parent / f'league_{league_id}_csv'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
