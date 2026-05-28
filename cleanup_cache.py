"""清掉 data/matches/ 里所有 leagueid != 19479 的缓存文件。

用途：玩家反查时 OpenDota 不真过滤 league_id，把别的联赛/公开匹配的详情也下回来了，
本脚本把它们删掉，让缓存只保留目标联赛的比赛。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    cfg_path = Path("config.json")
    if not cfg_path.exists():
        print("找不到 config.json")
        return 1
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    target_league = cfg.get("league_id")
    if not target_league:
        print("config.json 缺少 league_id")
        return 1

    cache_dir = Path("data/matches")
    files = [f for f in os.listdir(cache_dir)
             if f.endswith(".json") and not f.startswith("_")]
    print(f"扫描 {len(files)} 个缓存文件...")

    kept = 0
    removed = 0
    errors = 0
    for fname in files:
        fpath = cache_dir / fname
        try:
            with fpath.open("r", encoding="utf-8") as f:
                m = json.load(f)
            if m.get("leagueid") == target_league:
                kept += 1
            else:
                fpath.unlink()
                removed += 1
        except Exception as e:
            print(f"  跳过 {fname}: {e}")
            errors += 1

    print()
    print(f"保留 league={target_league}: {kept} 场")
    print(f"删除非该联赛:           {removed} 场")
    if errors:
        print(f"读取出错:               {errors}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
