"""账号密码认证。

路由：
    POST /api/register     注册（username + password + 选个 dota account_id）
    POST /api/login        登录
    POST /api/logout       退出
    GET  /api/me           查看当前登录态

Session 字段：
    account_id   dota account_id（也是 profiles 表的主键）
    username     用户名
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from web import db

bp = Blueprint("auth", __name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_一-鿿]{2,20}$")

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


def _bootstrap_admin_ids() -> set[int]:
    """config.json 里的 admins 名单（引导超管，防止把自己锁在外面）。每次读，量很小。"""
    try:
        cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return {int(x) for x in cfg.get("admins", [])}
    except Exception:
        return set()


def is_admin(account_id: int | None) -> bool:
    """管理员判定：config 引导名单 或 profiles.is_admin==1。"""
    if account_id is None:
        return False
    return account_id in _bootstrap_admin_ids() or db.is_account_admin(account_id)


@bp.route("/api/register", methods=["POST"])
def api_register():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    account_id = payload.get("account_id")

    # 校验
    if not USERNAME_RE.match(username):
        return jsonify({"error": "用户名必须 2-20 字符（字母数字下划线中文）"}), 400
    if len(password) < 6:
        return jsonify({"error": "密码至少 6 位"}), 400
    try:
        account_id = int(account_id)
    except (TypeError, ValueError):
        return jsonify({"error": "请选择你的玩家身份（account_id 必须是数字）"}), 400

    # 唯一性检查
    if db.is_username_taken(username):
        return jsonify({"error": f"用户名 {username} 已被占用"}), 409
    if db.is_account_registered(account_id):
        return jsonify({"error": "这个玩家已经被别人注册过了"}), 409

    try:
        db.create_account(account_id, username, generate_password_hash(password))
    except ValueError as e:
        return jsonify({"error": str(e)}), 409

    # 注册成功自动登录
    session["account_id"] = account_id
    session["username"] = username
    return jsonify({"ok": True, "account_id": account_id, "username": username})


@bp.route("/api/login", methods=["POST"])
def api_login():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""

    if not username or not password:
        return jsonify({"error": "请输入用户名和密码"}), 400

    user = db.get_account_by_username(username)
    if not user or not check_password_hash(user.get("password_hash") or "", password):
        return jsonify({"error": "用户名或密码不对"}), 401

    session["account_id"] = user["account_id"]
    session["username"] = username
    return jsonify({"ok": True, "account_id": user["account_id"], "username": username})


@bp.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


def current_user() -> tuple[int | None, str | None]:
    """返回 (account_id, username)，未登录返回 (None, None)。"""
    return session.get("account_id"), session.get("username")


def require_login():
    """如果未登录，返回 401 JSON 响应；否则返回 None。"""
    aid, _ = current_user()
    if aid is None:
        return jsonify({"error": "未登录"}), 401
    return None


def require_admin():
    """未登录返回 401；已登录但非管理员返回 403；管理员返回 None。"""
    aid, _ = current_user()
    if aid is None:
        return jsonify({"error": "未登录"}), 401
    if not is_admin(aid):
        return jsonify({"error": "需要管理员权限"}), 403
    return None
