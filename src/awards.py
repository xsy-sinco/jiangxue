"""单场比赛颁奖 + 综合评分 —— 前后端唯一的评分 / 颁奖口径。

历史上 index.html 和 player.html 各自维护了一份 _perMatchScore（v4）和
matchAwards，公式重复、容易跑偏。现在收口到后端：

- per_match_score()      单场玩家综合分（11 维度，v4 权重原样移植）
- compute_match_awards() 单场颁奖，返回 {奖项 -> 该场 players 列表里的下标}
- AWARD_META             奖项中文名 / emoji，前端 honor wall / 荣誉榜 / 对局徽章共用

前端只读 serialize 产出的 p.score / match.awards / player.awards，不再自己算。
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

WIN_BONUS = 100

# 奖项元数据（顺序即对局详情里颁奖卡的展示顺序）。
AWARD_META: list[dict[str, str]] = [
    {"key": "mvp",         "label": "综合 MVP", "emoji": "👑"},
    {"key": "carry",       "label": "最 C",     "emoji": "🚀"},
    {"key": "carried",     "label": "躺赢",     "emoji": "🛌"},
    {"key": "loserMvp",    "label": "败方 MVP", "emoji": "💔"},
    {"key": "mostKills",   "label": "最多击杀", "emoji": "⚔️"},
    {"key": "mostAssists", "label": "最多助攻", "emoji": "🤝"},
    {"key": "actor",       "label": "演员",     "emoji": "🎭"},
]
AWARD_KEYS: list[str] = [a["key"] for a in AWARD_META]


def per_match_score(p: dict[str, Any]) -> float:
    """单场玩家综合分（v4 carry/support 平衡版，权重见 git 历史）。

    p 是 serialize._match_player() 产出的玩家 dict。
    """
    return (
        (p.get("kills", 0) * 1.7 + p.get("assists", 0) * 2.0 - p.get("deaths", 0) * 0.5) * 80
        + (p.get("hero_damage", 0) or 0) / 100
        + (p.get("tower_damage", 0) or 0) / 45
        + (p.get("hero_healing", 0) or 0) / 80
        + (p.get("gpm", 0) or 0) * 1.2
        + (p.get("xpm", 0) or 0) * 0.8
        + (p.get("last_hits", 0) or 0) * 0.2
        + (p.get("denies", 0) or 0) * 2.5
        + (p.get("net_worth", 0) or 0) / 900
        + (p.get("level", 0) or 0) * 2.8
        + (WIN_BONUS if p.get("win") else 0)
    )


def _carry_score(p: dict[str, Any]) -> float:
    """最 C 专用：净值 + 英雄伤害（carry 特征）。"""
    return (p.get("net_worth", 0) or 0) / 1000 + (p.get("hero_damage", 0) or 0) / 100


def _pick(items: Sequence[tuple[int, dict[str, Any]]],
          key_fn: Callable[[dict[str, Any]], float],
          largest: bool = True) -> int:
    """从 (下标, 玩家) 列表里挑极值，返回原始下标。

    平局取靠前者（与前端 Array.reduce 的 `fn(b) > fn(a)` 行为一致）。
    """
    best_i, best_p = items[0]
    best_v = key_fn(best_p)
    for i, p in items[1:]:
        v = key_fn(p)
        if (v > best_v) if largest else (v < best_v):
            best_i, best_v = i, v
    return best_i


def compute_match_awards(players: list[dict[str, Any]]) -> dict[str, int] | None:
    """单场颁奖。

    players: serialize._match_player() 产出的本场玩家 dict 列表。
    返回 {award_key: players 下标}；缺胜方或败方时返回 None（不颁奖）。
    用下标而非整份 dict，是为了不让 aggregate.json 体积翻倍。
    """
    if not players:
        return None
    winners = [(i, p) for i, p in enumerate(players) if p.get("win")]
    losers = [(i, p) for i, p in enumerate(players) if not p.get("win")]
    if not winners or not losers:
        return None
    everyone = list(enumerate(players))
    return {
        "mvp":         _pick(winners, per_match_score),
        "carry":       _pick(winners, _carry_score),
        "carried":     _pick(winners, per_match_score, largest=False),
        "loserMvp":    _pick(losers, per_match_score),
        "mostKills":   _pick(everyone, lambda p: p.get("kills", 0)),
        "mostAssists": _pick(everyone, lambda p: p.get("assists", 0)),
        "actor":       _pick(everyone, lambda p: p.get("deaths", 0)),
    }
