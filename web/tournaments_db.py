"""赛事板块的 community.db 读写（复用 web.db.get_conn）。

四张表：tournaments / tournament_signups / teams / tournament_matches，
schema 定义在 web/db.py:SCHEMA，这里只放查询/写入函数。
选手名字/头像不在这里（在 data/ 的 aggregate.json），前端按 account_id 自行映射。
"""

from __future__ import annotations

import time
from typing import Any

from web.db import get_conn


def _now() -> int:
    return int(time.time())


# ============== tournaments ==============

def list_tournaments() -> list[dict[str, Any]]:
    with get_conn() as c:
        rows = c.execute("SELECT * FROM tournaments ORDER BY created_at DESC, id DESC").fetchall()
    return [dict(r) for r in rows]


def get_tournament(tid: int) -> dict[str, Any] | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM tournaments WHERE id = ?", (tid,)).fetchone()
    return dict(r) if r else None


def create_tournament(name: str, description: str | None = None,
                      per_team_budget: int = 1000, created_by: int | None = None,
                      cover_url: str | None = None) -> dict[str, Any]:
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO tournaments (name, description, status, per_team_budget, cover_url, created_by, created_at) "
            "VALUES (?, ?, 'registration', ?, ?, ?, ?)",
            (name, description, per_team_budget, cover_url, created_by, _now()),
        )
        r = c.execute("SELECT * FROM tournaments WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(r)


_UPDATABLE = {"name", "description", "status", "per_team_budget", "cover_url"}


def update_tournament(tid: int, **fields: Any) -> dict[str, Any] | None:
    clean = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if clean:
        cols = ", ".join(f"{k} = ?" for k in clean)
        with get_conn() as c:
            c.execute(f"UPDATE tournaments SET {cols} WHERE id = ?", [*clean.values(), tid])
    return get_tournament(tid)


def delete_tournament(tid: int) -> None:
    with get_conn() as c:
        for tbl in ("tournament_signups", "teams", "tournament_matches"):
            c.execute(f"DELETE FROM {tbl} WHERE tournament_id = ?", (tid,))
        c.execute("DELETE FROM tournaments WHERE id = ?", (tid,))


# ============== signups（报名 + 身价 + 队伍归属）==============

def list_signups(tid: int) -> list[dict[str, Any]]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM tournament_signups WHERE tournament_id = ? ORDER BY id", (tid,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_signup(tid: int, account_id: int) -> dict[str, Any] | None:
    with get_conn() as c:
        r = c.execute(
            "SELECT * FROM tournament_signups WHERE tournament_id = ? AND account_id = ?",
            (tid, account_id),
        ).fetchone()
    return dict(r) if r else None


def add_signup(tid: int, account_id: int) -> None:
    with get_conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO tournament_signups (tournament_id, account_id, created_at) VALUES (?, ?, ?)",
            (tid, account_id, _now()),
        )


def remove_signup(tid: int, account_id: int) -> None:
    with get_conn() as c:
        c.execute(
            "DELETE FROM tournament_signups WHERE tournament_id = ? AND account_id = ?",
            (tid, account_id),
        )


def set_valuation(tid: int, account_id: int, valuation: int | None) -> None:
    with get_conn() as c:
        c.execute(
            "UPDATE tournament_signups SET valuation = ? WHERE tournament_id = ? AND account_id = ?",
            (valuation, tid, account_id),
        )


def set_captain(tid: int, account_id: int, is_captain: bool) -> None:
    with get_conn() as c:
        c.execute(
            "UPDATE tournament_signups SET is_captain = ? WHERE tournament_id = ? AND account_id = ?",
            (1 if is_captain else 0, tid, account_id),
        )


def assign_player(tid: int, account_id: int, team_id: int, price: int) -> None:
    with get_conn() as c:
        c.execute(
            "UPDATE tournament_signups SET team_id = ?, auction_price = ? "
            "WHERE tournament_id = ? AND account_id = ?",
            (team_id, price, tid, account_id),
        )


def unassign_player(tid: int, account_id: int) -> None:
    with get_conn() as c:
        c.execute(
            "UPDATE tournament_signups SET team_id = NULL, auction_price = NULL "
            "WHERE tournament_id = ? AND account_id = ?",
            (tid, account_id),
        )


def team_spent(tid: int, team_id: int) -> int:
    """某队已花费（成交价之和）。"""
    with get_conn() as c:
        r = c.execute(
            "SELECT COALESCE(SUM(auction_price), 0) AS s FROM tournament_signups "
            "WHERE tournament_id = ? AND team_id = ?",
            (tid, team_id),
        ).fetchone()
    return r["s"] or 0


# ============== teams ==============

def list_teams(tid: int) -> list[dict[str, Any]]:
    with get_conn() as c:
        rows = c.execute("SELECT * FROM teams WHERE tournament_id = ? ORDER BY id", (tid,)).fetchall()
    return [dict(r) for r in rows]


def get_team(team_id: int) -> dict[str, Any] | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    return dict(r) if r else None


def create_team(tid: int, name: str, captain_account_id: int | None = None,
                budget: int | None = None) -> dict[str, Any]:
    if budget is None:  # 默认用赛事的每队预算
        t = get_tournament(tid)
        budget = (t or {}).get("per_team_budget")
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO teams (tournament_id, name, captain_account_id, budget, created_at) VALUES (?, ?, ?, ?, ?)",
            (tid, name, captain_account_id, budget, _now()),
        )
        r = c.execute("SELECT * FROM teams WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(r)


def update_team(team_id: int, name: str | None = None, captain_account_id: int | None = None,
                budget: int | None = None) -> None:
    sets, vals = [], []
    if name is not None:
        sets.append("name = ?"); vals.append(name)
    if captain_account_id is not None:
        sets.append("captain_account_id = ?"); vals.append(captain_account_id)
    if budget is not None:
        sets.append("budget = ?"); vals.append(budget)
    if sets:
        vals.append(team_id)
        with get_conn() as c:
            c.execute(f"UPDATE teams SET {', '.join(sets)} WHERE id = ?", vals)


def team_budget(tid: int, team: dict[str, Any]) -> int:
    """队伍预算：自身 budget 优先，NULL 回退赛事 per_team_budget。"""
    if team.get("budget") is not None:
        return team["budget"]
    return (get_tournament(tid) or {}).get("per_team_budget") or 0


def delete_team(team_id: int) -> None:
    with get_conn() as c:
        # 解散队伍：把该队选手的归属清空
        c.execute(
            "UPDATE tournament_signups SET team_id = NULL, auction_price = NULL WHERE team_id = ?",
            (team_id,),
        )
        c.execute("UPDATE tournament_signups SET is_captain = 0 WHERE team_id = ?", (team_id,))
        c.execute("DELETE FROM teams WHERE id = ?", (team_id,))


# ============== matches（赛程节点）==============

def list_matches(tid: int) -> list[dict[str, Any]]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM tournament_matches WHERE tournament_id = ? ORDER BY order_idx, id", (tid,)
        ).fetchall()
    return [dict(r) for r in rows]


def create_match(tid: int, **fields: Any) -> dict[str, Any]:
    f = {
        "round_name": fields.get("round_name") or "",
        "order_idx": fields.get("order_idx") or 0,
        "team_a_id": fields.get("team_a_id"),
        "team_b_id": fields.get("team_b_id"),
        "score_a": fields.get("score_a") or 0,
        "score_b": fields.get("score_b") or 0,
        "winner_team_id": fields.get("winner_team_id"),
        "best_of": fields.get("best_of") or 1,
        "scheduled_time": fields.get("scheduled_time"),
    }
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO tournament_matches "
            "(tournament_id, round_name, order_idx, team_a_id, team_b_id, score_a, score_b, "
            " winner_team_id, best_of, scheduled_time, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, f["round_name"], f["order_idx"], f["team_a_id"], f["team_b_id"],
             f["score_a"], f["score_b"], f["winner_team_id"], f["best_of"], f["scheduled_time"], _now()),
        )
        r = c.execute("SELECT * FROM tournament_matches WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(r)


_MATCH_FIELDS = {"round_name", "order_idx", "team_a_id", "team_b_id", "score_a", "score_b",
                 "winner_team_id", "best_of", "scheduled_time"}


def update_match(match_id: int, **fields: Any) -> None:
    clean = {k: v for k, v in fields.items() if k in _MATCH_FIELDS}
    if clean:
        cols = ", ".join(f"{k} = ?" for k in clean)
        with get_conn() as c:
            c.execute(f"UPDATE tournament_matches SET {cols} WHERE id = ?", [*clean.values(), match_id])


def delete_match(match_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM tournament_matches WHERE id = ?", (match_id,))


def get_detail(tid: int) -> dict[str, Any] | None:
    t = get_tournament(tid)
    if not t:
        return None
    return {
        "tournament": t,
        "signups": list_signups(tid),
        "teams": list_teams(tid),
        "matches": list_matches(tid),
    }
