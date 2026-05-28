"""仅从本地缓存重新生成 data/aggregate.json，不走任何网络。

用途：改了 src/stats.py 或 src/serialize.py 输出格式后，快速重新聚合。
不会发现新比赛、不会获取新详情；只把现有缓存重新算一遍。

用法：python regen_aggregate.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from src.api import OpenDotaClient
from src.heroes import HeroIndex
from src.serialize import serialize
from src.stats import aggregate


def main() -> int:
    cfg_path = Path("config.json")
    if not cfg_path.exists():
        print("找不到 config.json")
        return 1
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    cache_dir = Path("data/matches")
    if not cache_dir.exists():
        print(f"找不到 {cache_dir}")
        return 1

    # HeroIndex 需要英雄常量；也走缓存（_heroes.json）
    client = OpenDotaClient(cache_dir=cache_dir, api_key="")
    hero_index = HeroIndex(client.get_heroes())

    matches: list[dict] = []
    skipped = 0
    for fname in sorted(os.listdir(cache_dir)):
        if not fname.endswith(".json") or fname.startswith("_"):
            continue
        try:
            with (cache_dir / fname).open("r", encoding="utf-8") as f:
                m = json.load(f)
            if not m.get("players"):
                skipped += 1
                continue
            matches.append(m)
        except Exception as e:
            print(f"  跳过 {fname}: {e}")
            skipped += 1

    print(f"加载缓存：{len(matches)} 场比赛（跳过 {skipped} 个无效文件）")

    result = aggregate(matches, hero_index, cfg.get("player_aliases"),
                       league_id=cfg.get("league_id"))
    data = serialize(result, hero_index, cfg.get("league_id", 0), "")

    out_path = Path("data/aggregate.json")
    out_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    size_kb = out_path.stat().st_size / 1024
    print(f"已写入 {out_path}（{size_kb:.0f} KB，{len(data['matches'])} 场，{len(data['players'])} 玩家）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
