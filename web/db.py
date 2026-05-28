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
HIGHLIGHT_DIR = DB_DIR / "uploads" / "highlights"

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
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    description   TEXT,
    filename      TEXT NOT NULL,
    original_name TEXT,
    size_bytes    INTEGER,
    status        TEXT,           -- ready / processing / failed
    created_by    INTEGER,
    created_at    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_highlights_created ON highlights(created_at DESC);
"""

# 允许通过 POST /api/me/profile 更新的字段（用户名/密码走单独接口）
EDITABLE_FIELDS = {"avatar_url", "display_name", "bio", "signature", "positions", "custom_tags"}


def init_db() -> None:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    (DB_DIR / "uploads" / "avatars").mkdir(parents=True, exist_ok=True)
    (DB_DIR / "uploads" / "highlights").mkdir(parents=True, exist_ok=True)
    with get_conn() as c:
        c.executescript(SCHEMA)
        # 旧 DB 没有 positions 列时无痛迁移
        try:
            c.execute("ALTER TABLE profiles ADD COLUMN positions TEXT")
        except sqlite3.OperationalError:
            pass  # 已存在
        # 旧 DB 没有 highlights.status 列时无痛迁移
        try:
            c.execute("ALTER TABLE highlights ADD COLUMN status TEXT")
        except sqlite3.OperationalError:
            pass  # 已存在


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

def _row_to_highlight(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["status"] = d.get("status") or "ready"  # 旧记录 NULL 视为 ready
    return d


def list_highlights() -> list[dict[str, Any]]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM highlights ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [_row_to_highlight(r) for r in rows]


def create_highlight(
    title: str,
    filename: str,
    original_name: str | None = None,
    size_bytes: int | None = None,
    description: str | None = None,
    created_by: int | None = None,
    status: str = "ready",
) -> dict[str, Any]:
    now = int(time.time())
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO highlights "
            "(title, description, filename, original_name, size_bytes, status, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (title, description, filename, original_name, size_bytes, status, created_by, now),
        )
        row = c.execute("SELECT * FROM highlights WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_highlight(row)


def get_highlight(highlight_id: int) -> dict[str, Any] | None:
    with get_conn() as c:
        row = c.execute("SELECT * FROM highlights WHERE id = ?", (highlight_id,)).fetchone()
    return _row_to_highlight(row) if row else None


def set_highlight_status(
    highlight_id: int,
    status: str,
    filename: str | None = None,
    size_bytes: int | None = None,
) -> None:
    """转码 worker 用：更新状态，必要时同步替换后的文件名 / 大小。"""
    sets = ["status = ?"]
    vals: list[Any] = [status]
    if filename is not None:
        sets.append("filename = ?")
        vals.append(filename)
    if size_bytes is not None:
        sets.append("size_bytes = ?")
        vals.append(size_bytes)
    vals.append(highlight_id)
    with get_conn() as c:
        c.execute(f"UPDATE highlights SET {', '.join(sets)} WHERE id = ?", vals)


def delete_highlight(highlight_id: int, current_account_id: int) -> str | None:
    """只允许创建者删除。返回被删记录的 filename（供调用方清理磁盘文件），
    找不到或无权时返回 None。"""
    with get_conn() as c:
        row = c.execute(
            "SELECT created_by, filename FROM highlights WHERE id = ?", (highlight_id,)
        ).fetchone()
        if not row or row["created_by"] != current_account_id:
            return None
        c.execute("DELETE FROM highlights WHERE id = ?", (highlight_id,))
    return row["filename"]


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
