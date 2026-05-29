"""社区数据 SQLite 存储。

存玩家自定义资料 + 账号密码。所有内容跟 data/ 完全隔离：
- data/        → sync.bat 推（战绩自动同步）
- community/   → 服务器原生（用户自定义内容，不被覆盖）
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_DIR = ROOT / "community"
DB_PATH = DB_DIR / "community.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    account_id    INTEGER PRIMARY KEY,
    username      TEXT UNIQUE,
    password_hash TEXT,
    avatar_url    TEXT,
    display_name  TEXT,
    bio           TEXT,
    signature     TEXT,
    position      INTEGER,
    positions     TEXT,
    custom_tags   TEXT,
    mmr           INTEGER,
    is_admin      INTEGER DEFAULT 0,
    created_at    INTEGER,
    updated_at    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_profiles_username ON profiles(username);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    event_date   TEXT,
    bvid         TEXT,
    url          TEXT,
    description  TEXT,
    participants TEXT,
    cover_url    TEXT,
    created_by   INTEGER,
    created_at   INTEGER,
    updated_at   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date DESC);

CREATE TABLE IF NOT EXISTS highlights (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    description  TEXT,
    bvid         TEXT,
    url          TEXT,
    created_by   INTEGER,
    created_at   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_highlights_created ON highlights(created_at DESC);

CREATE TABLE IF NOT EXISTS tournaments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    description     TEXT,
    status          TEXT DEFAULT 'registration',  -- registration / auction / bracket / finished
    per_team_budget INTEGER DEFAULT 1000,
    cover_url       TEXT,
    created_by      INTEGER,
    created_at      INTEGER
);

CREATE TABLE IF NOT EXISTS tournament_signups (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL,
    account_id    INTEGER NOT NULL,
    valuation     INTEGER,          -- 身价（管理员设，参考值）
    is_captain    INTEGER DEFAULT 0,
    team_id       INTEGER,          -- 被拍下后归属的队伍
    auction_price INTEGER,          -- 成交价
    created_at    INTEGER,
    UNIQUE(tournament_id, account_id)
);

CREATE INDEX IF NOT EXISTS idx_signups_tour ON tournament_signups(tournament_id);

CREATE TABLE IF NOT EXISTS teams (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id      INTEGER NOT NULL,
    name               TEXT NOT NULL,
    captain_account_id INTEGER,
    budget             INTEGER,          -- 该队预算；NULL 时回退赛事默认 per_team_budget
    created_at         INTEGER
);

CREATE INDEX IF NOT EXISTS idx_teams_tour ON teams(tournament_id);

CREATE TABLE IF NOT EXISTS tournament_matches (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id  INTEGER NOT NULL,
    round_name     TEXT,             -- 如 小组赛 / 胜者组R1 / 决赛
    order_idx      INTEGER DEFAULT 0,
    team_a_id      INTEGER,
    team_b_id      INTEGER,
    score_a        INTEGER DEFAULT 0,
    score_b        INTEGER DEFAULT 0,
    winner_team_id INTEGER,
    best_of        INTEGER DEFAULT 1,
    scheduled_time TEXT,
    dota_match_ids TEXT,             -- 预留：关联的真实 match_id
    created_at     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_tmatches_tour ON tournament_matches(tournament_id, order_idx);
"""

# 允许通过 POST /api/me/profile 更新的字段（用户名/密码走单独接口）
EDITABLE_FIELDS = {"avatar_url", "display_name", "bio", "signature", "positions", "custom_tags", "mmr"}


def init_db() -> None:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    (DB_DIR / "uploads" / "avatars").mkdir(parents=True, exist_ok=True)
    (DB_DIR / "uploads" / "tournaments").mkdir(parents=True, exist_ok=True)
    with get_conn() as c:
        # 集锦从"自传视频"改为"B站嵌入"：老表(含 filename 列)直接重建，顺带清空旧数据
        cols = [r[1] for r in c.execute("PRAGMA table_info(highlights)").fetchall()]
        if cols and "filename" in cols:
            c.execute("DROP TABLE highlights")
        c.executescript(SCHEMA)
        # 旧 DB 无痛迁移：补列
        for ddl in (
            "ALTER TABLE profiles ADD COLUMN positions TEXT",
            "ALTER TABLE profiles ADD COLUMN is_admin INTEGER DEFAULT 0",
            "ALTER TABLE profiles ADD COLUMN mmr INTEGER",
            "ALTER TABLE tournaments ADD COLUMN cover_url TEXT",
            "ALTER TABLE teams ADD COLUMN budget INTEGER",
        ):
            try:
                c.execute(ddl)
            except sqlite3.OperationalError:
                pass  # 已存在/表还没建


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row_to_profile(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    # 永不向外吐密码 hash
    d.pop("password_hash", None)
    if d.get("custom_tags"):
        try:
            d["custom_tags"] = json.loads(d["custom_tags"])
        except Exception:
            d["custom_tags"] = []
    else:
        d["custom_tags"] = []
    # positions: 新版多选；旧版单选 position 自动兼容
    if d.get("positions"):
        try:
            d["positions"] = json.loads(d["positions"])
        except Exception:
            d["positions"] = []
    elif d.get("position"):
        d["positions"] = [d["position"]]
    else:
        d["positions"] = []
    return d


def get_profile(account_id: int) -> dict[str, Any] | None:
    with get_conn() as c:
        row = c.execute("SELECT * FROM profiles WHERE account_id = ?", (account_id,)).fetchone()
    return _row_to_profile(row)


def get_all_profiles() -> dict[int, dict[str, Any]]:
    with get_conn() as c:
        rows = c.execute("SELECT * FROM profiles").fetchall()
    return {row["account_id"]: _row_to_profile(row) for row in rows}


def get_account_by_username(username: str) -> dict[str, Any] | None:
    """登录用：返回完整记录（含 password_hash），供 auth.py 校验密码用。"""
    with get_conn() as c:
        row = c.execute("SELECT * FROM profiles WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def is_username_taken(username: str) -> bool:
    with get_conn() as c:
        row = c.execute("SELECT 1 FROM profiles WHERE username = ?", (username,)).fetchone()
    return row is not None


def is_account_registered(account_id: int) -> bool:
    """检查这个玩家是否已经被人注册过了。"""
    with get_conn() as c:
        row = c.execute(
            "SELECT username FROM profiles WHERE account_id = ? AND username IS NOT NULL",
            (account_id,),
        ).fetchone()
    return row is not None


def create_account(account_id: int, username: str, password_hash: str) -> None:
    now = int(time.time())
    with get_conn() as c:
        existing = c.execute(
            "SELECT username FROM profiles WHERE account_id = ?", (account_id,)
        ).fetchone()
        if existing and existing["username"]:
            raise ValueError(f"account_id {account_id} 已经被 {existing['username']} 注册")
        if existing:
            c.execute(
                "UPDATE profiles SET username = ?, password_hash = ?, updated_at = ? WHERE account_id = ?",
                (username, password_hash, now, account_id),
            )
        else:
            c.execute(
                "INSERT INTO profiles (account_id, username, password_hash, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (account_id, username, password_hash, now, now),
            )


# ============== ADMIN ==============

def is_account_admin(account_id: int) -> bool:
    with get_conn() as c:
        row = c.execute("SELECT is_admin FROM profiles WHERE account_id = ?", (account_id,)).fetchone()
    return bool(row and row["is_admin"])


def set_admin(account_id: int, is_admin: bool) -> None:
    """给某账号开/撤管理员权限。没有 profile 行就建一个最小行。"""
    now = int(time.time())
    flag = 1 if is_admin else 0
    with get_conn() as c:
        existing = c.execute("SELECT account_id FROM profiles WHERE account_id = ?", (account_id,)).fetchone()
        if existing:
            c.execute("UPDATE profiles SET is_admin = ?, updated_at = ? WHERE account_id = ?",
                      (flag, now, account_id))
        else:
            c.execute("INSERT INTO profiles (account_id, is_admin, created_at, updated_at) VALUES (?, ?, ?, ?)",
                      (account_id, flag, now, now))


def list_admin_account_ids() -> list[int]:
    with get_conn() as c:
        rows = c.execute("SELECT account_id FROM profiles WHERE is_admin = 1").fetchall()
    return [r["account_id"] for r in rows]


# ============== EVENTS ==============

import re
_BVID_RE = re.compile(r"(BV[0-9A-Za-z]{10})")


def extract_bvid(url: str) -> str | None:
    """从 B 站链接里抠 BVID，例如 BV1MWXjB4EH5"""
    if not url:
        return None
    m = _BVID_RE.search(url)
    return m.group(1) if m else None


def _row_to_event(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    if d.get("participants"):
        try:
            d["participants"] = json.loads(d["participants"])
        except Exception:
            d["participants"] = []
    else:
        d["participants"] = []
    return d


def list_events() -> list[dict[str, Any]]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM events ORDER BY event_date DESC, id DESC"
        ).fetchall()
    return [_row_to_event(r) for r in rows]


def create_event(
    title: str,
    url: str,
    event_date: str | None = None,
    description: str | None = None,
    participants: list[str] | None = None,
    cover_url: str | None = None,
    created_by: int | None = None,
) -> dict[str, Any]:
    bvid = extract_bvid(url)
    now = int(time.time())
    part_json = json.dumps(participants or [], ensure_ascii=False)
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO events "
            "(title, event_date, bvid, url, description, participants, cover_url, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, event_date, bvid, url, description, part_json, cover_url, created_by, now, now),
        )
        eid = cur.lastrowid
        row = c.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
    return _row_to_event(row)


def delete_event(event_id: int, current_account_id: int) -> bool:
    """只允许创建者本人删除。返回是否真的删了。"""
    with get_conn() as c:
        row = c.execute("SELECT created_by FROM events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            return False
        if row["created_by"] != current_account_id:
            return False
        c.execute("DELETE FROM events WHERE id = ?", (event_id,))
    return True


# ============== HIGHLIGHTS (集锦) ==============
# 跟歌友会(events)一样：存 B 站链接 + BVID，前端嵌 bilibili 播放器。

def list_highlights() -> list[dict[str, Any]]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM highlights ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def create_highlight(
    title: str,
    url: str,
    description: str | None = None,
    created_by: int | None = None,
) -> dict[str, Any]:
    bvid = extract_bvid(url)
    now = int(time.time())
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO highlights (title, description, bvid, url, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (title, description, bvid, url, created_by, now),
        )
        row = c.execute("SELECT * FROM highlights WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def delete_highlight(highlight_id: int, current_account_id: int) -> bool:
    """只允许创建者删除。返回是否真的删了。"""
    with get_conn() as c:
        row = c.execute("SELECT created_by FROM highlights WHERE id = ?", (highlight_id,)).fetchone()
        if not row or row["created_by"] != current_account_id:
            return False
        c.execute("DELETE FROM highlights WHERE id = ?", (highlight_id,))
    return True


def upsert_profile(account_id: int, **fields: Any) -> dict[str, Any] | None:
    """更新玩家资料。只接受 EDITABLE_FIELDS 里的字段。"""
    clean: dict[str, Any] = {}
    for k, v in fields.items():
        if k not in EDITABLE_FIELDS:
            continue
        if k in ("custom_tags", "positions") and v is not None and not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        clean[k] = v
    if not clean:
        return get_profile(account_id)
    now = int(time.time())

    with get_conn() as c:
        existing = c.execute(
            "SELECT account_id FROM profiles WHERE account_id = ?", (account_id,)
        ).fetchone()
        if existing:
            clean["updated_at"] = now
            cols = ", ".join(f"{k} = ?" for k in clean)
            c.execute(
                f"UPDATE profiles SET {cols} WHERE account_id = ?",
                [*clean.values(), account_id],
            )
        else:
            clean["account_id"] = account_id
            clean["created_at"] = now
            clean["updated_at"] = now
            cols = ", ".join(clean.keys())
            placeholders = ", ".join("?" * len(clean))
            c.execute(
                f"INSERT INTO profiles ({cols}) VALUES ({placeholders})",
                list(clean.values()),
            )
    return get_profile(account_id)
