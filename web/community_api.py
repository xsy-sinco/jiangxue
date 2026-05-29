"""社区 API：玩家资料读写、头像上传。

路由前缀 /api：
    GET  /api/me                  当前登录用户信息（含资料）
    POST /api/me/profile          更新自己的资料（必须登录）
    POST /api/me/avatar           上传自己的头像（multipart/form-data）
    GET  /api/profiles            所有玩家资料的 map（账号 ID → 资料）
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory

from web import auth, db

bp = Blueprint("community", __name__)

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "community" / "uploads" / "avatars"
ALLOWED_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
MAX_AVATAR_SIZE = 5 * 1024 * 1024  # 5MB


@bp.route("/api/me")
def api_me():
    aid, username = auth.current_user()
    if aid is None:
        return jsonify({"logged_in": False})
    return jsonify({
        "logged_in": True,
        "account_id": aid,
        "username": username,
        "profile": db.get_profile(aid),
        "is_admin": auth.is_admin(aid),
    })


@bp.route("/api/admins")
def api_list_admins():
    err = auth.require_admin()
    if err:
        return err
    profiles = db.get_all_profiles()
    out = []
    for a in db.list_admin_account_ids():
        p = profiles.get(a) or {}
        out.append({"account_id": a, "username": p.get("username"), "display_name": p.get("display_name")})
    return jsonify(out)


@bp.route("/api/admins", methods=["POST"])
def api_set_admin():
    """管理员给某账号开/撤管理员权限。"""
    err = auth.require_admin()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    try:
        target = int(payload.get("account_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "account_id 必须是数字"}), 400
    db.set_admin(target, bool(payload.get("grant", True)))
    return jsonify({"ok": True})


@bp.route("/api/profiles")
def api_profiles():
    return jsonify(db.get_all_profiles())


@bp.route("/api/me/profile", methods=["POST"])
def api_update_profile():
    err = auth.require_login()
    if err:
        return err
    aid, _ = auth.current_user()
    payload = request.get_json(silent=True) or {}

    # 白名单字段
    fields = {k: payload[k] for k in db.EDITABLE_FIELDS if k in payload}

    # 简单校验
    if "bio" in fields and isinstance(fields["bio"], str) and len(fields["bio"]) > 1000:
        return jsonify({"error": "bio 太长（最多 1000 字）"}), 400
    if "signature" in fields and isinstance(fields["signature"], str) and len(fields["signature"]) > 100:
        return jsonify({"error": "signature 太长（最多 100 字）"}), 400
    if "positions" in fields:
        pos = fields["positions"] or []
        if not isinstance(pos, list) or len(pos) > 5:
            return jsonify({"error": "positions 必须是数组，最多 5 个"}), 400
        if any(p not in (1, 2, 3, 4, 5) for p in pos):
            return jsonify({"error": "positions 元素必须是 1-5"}), 400
        # 去重保序
        fields["positions"] = list(dict.fromkeys(pos))
    if "custom_tags" in fields:
        tags = fields["custom_tags"]
        if not isinstance(tags, list) or len(tags) > 10:
            return jsonify({"error": "custom_tags 必须是数组，最多 10 个"}), 400
        if any(not isinstance(t, str) or len(t) > 20 for t in tags):
            return jsonify({"error": "每个 tag 最多 20 字"}), 400
    if "mmr" in fields:
        v = fields["mmr"]
        if v in (None, ""):
            fields["mmr"] = None
        else:
            try:
                v = int(v)
            except (TypeError, ValueError):
                return jsonify({"error": "MMR 必须是数字"}), 400
            if not (0 <= v <= 20000):
                return jsonify({"error": "MMR 超出合理范围"}), 400
            fields["mmr"] = v

    # 头像 URL 只能是相对路径（防止用户写第三方 URL）
    fields.pop("avatar_url", None)

    profile = db.upsert_profile(aid, **fields)
    return jsonify({"ok": True, "profile": profile})


@bp.route("/api/me/avatar", methods=["POST"])
def api_upload_avatar():
    err = auth.require_login()
    if err:
        return err
    aid, _ = auth.current_user()

    if "avatar" not in request.files:
        return jsonify({"error": "缺少 avatar 文件字段"}), 400
    f = request.files["avatar"]
    if not f.filename:
        return jsonify({"error": "未选择文件"}), 400

    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_EXTS:
        return jsonify({"error": f"格式不支持，仅允许 {', '.join(ALLOWED_EXTS)}"}), 400

    # 校验大小
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    if size > MAX_AVATAR_SIZE:
        return jsonify({"error": f"文件太大（{size//1024} KB > 5 MB）"}), 400

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # 覆盖式存储：每个玩家始终用 <account_id>.<ext>
    # 删掉旧的其他扩展名文件
    for old in UPLOAD_DIR.glob(f"{aid}.*"):
        try:
            old.unlink()
        except Exception:
            pass

    filename = f"{aid}.{ext}"
    f.save(str(UPLOAD_DIR / filename))
    avatar_url = f"/uploads/avatars/{filename}"

    profile = db.upsert_profile(aid, avatar_url=avatar_url)
    return jsonify({"ok": True, "avatar_url": avatar_url, "profile": profile})


@bp.route("/uploads/avatars/<path:filename>")
def serve_avatar(filename):
    return send_from_directory(str(UPLOAD_DIR), filename)


# ============== EVENTS (歌友会) ==============

@bp.route("/api/events")
def api_list_events():
    return jsonify(db.list_events())


@bp.route("/api/events", methods=["POST"])
def api_create_event():
    err = auth.require_login()
    if err:
        return err
    aid, _ = auth.current_user()
    payload = request.get_json(silent=True) or {}

    title = (payload.get("title") or "").strip()
    url = (payload.get("url") or "").strip()
    if not title:
        return jsonify({"error": "标题不能为空"}), 400
    if not url:
        return jsonify({"error": "链接不能为空"}), 400
    if len(title) > 100:
        return jsonify({"error": "标题最多 100 字"}), 400
    if not db.extract_bvid(url):
        return jsonify({"error": "无法识别 B 站 BVID（请确认链接含 BV 开头的 ID）"}), 400

    desc = (payload.get("description") or "").strip() or None
    if desc and len(desc) > 500:
        return jsonify({"error": "描述最多 500 字"}), 400
    event_date = (payload.get("event_date") or "").strip() or None
    participants = payload.get("participants") or []
    if not isinstance(participants, list) or len(participants) > 20:
        return jsonify({"error": "参与人数组最多 20 个"}), 400

    event = db.create_event(
        title=title, url=url, event_date=event_date,
        description=desc, participants=participants, created_by=aid,
    )
    return jsonify({"ok": True, "event": event})


@bp.route("/api/events/<int:event_id>", methods=["DELETE"])
def api_delete_event(event_id):
    err = auth.require_login()
    if err:
        return err
    aid, _ = auth.current_user()
    ok = db.delete_event(event_id, aid)
    if not ok:
        return jsonify({"error": "找不到该事件，或你不是创建者"}), 403
    return jsonify({"ok": True})


# ============== HIGHLIGHTS (集锦) ==============
# 跟歌友会一样：存 B 站链接 + BVID，前端嵌 bilibili 播放器（不再自建上传/转码）。

@bp.route("/api/highlights")
def api_list_highlights():
    return jsonify(db.list_highlights())


@bp.route("/api/highlights", methods=["POST"])
def api_create_highlight():
    err = auth.require_login()
    if err:
        return err
    aid, _ = auth.current_user()
    payload = request.get_json(silent=True) or {}

    title = (payload.get("title") or "").strip()
    url = (payload.get("url") or "").strip()
    if not title:
        return jsonify({"error": "标题不能为空"}), 400
    if not url:
        return jsonify({"error": "链接不能为空"}), 400
    if len(title) > 100:
        return jsonify({"error": "标题最多 100 字"}), 400
    if not db.extract_bvid(url):
        return jsonify({"error": "无法识别 B 站 BVID（请确认链接含 BV 开头的 ID）"}), 400

    desc = (payload.get("description") or "").strip() or None
    if desc and len(desc) > 500:
        return jsonify({"error": "描述最多 500 字"}), 400

    hl = db.create_highlight(title=title, url=url, description=desc, created_by=aid)
    return jsonify({"ok": True, "highlight": hl})


@bp.route("/api/highlights/<int:highlight_id>", methods=["DELETE"])
def api_delete_highlight(highlight_id):
    err = auth.require_login()
    if err:
        return err
    aid, _ = auth.current_user()
    if not db.delete_highlight(highlight_id, aid):
        return jsonify({"error": "找不到该集锦，或你不是创建者"}), 403
    return jsonify({"ok": True})
