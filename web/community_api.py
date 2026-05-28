"""社区 API：玩家资料读写、头像上传。

路由前缀 /api：
    GET  /api/me                  当前登录用户信息（含资料）
    POST /api/me/profile          更新自己的资料（必须登录）
    POST /api/me/avatar           上传自己的头像（multipart/form-data）
    GET  /api/profiles            所有玩家资料的 map（账号 ID → 资料）
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory

from web import auth, db, transcode

bp = Blueprint("community", __name__)

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "community" / "uploads" / "avatars"
ALLOWED_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
MAX_AVATAR_SIZE = 5 * 1024 * 1024  # 5MB

# ---------- 集锦视频上传配置（默认值可用环境变量覆盖） ----------
_MB = 1024 * 1024
HIGHLIGHT_DIR = db.HIGHLIGHT_DIR
ALLOWED_VIDEO_EXTS = {"mp4", "webm", "mov", "m4v"}  # mp4/webm 浏览器原生最稳
# 单文件上限（默认 200MB）
HIGHLIGHT_MAX_BYTES = int(os.environ.get("HIGHLIGHT_MAX_MB", "200")) * _MB
# 集锦目录总量上限（默认 3GB）
HIGHLIGHT_TOTAL_CAP = int(os.environ.get("HIGHLIGHT_TOTAL_MB", "3072")) * _MB
# 始终保留的磁盘空闲（默认 2GB）：低于这个值就拒绝新上传，避免把服务器塞满
HIGHLIGHT_MIN_FREE = int(os.environ.get("HIGHLIGHT_MIN_FREE_MB", "2048")) * _MB


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.glob("*") if f.is_file())


def _free_disk(path: Path) -> int:
    try:
        target = path if path.exists() else path.parent
        return shutil.disk_usage(str(target)).free
    except Exception:
        return HIGHLIGHT_MIN_FREE  # 拿不到就别用 free 这条规则卡人


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
    })


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

def _highlight_url(filename: str) -> str:
    return f"/uploads/highlights/{filename}"


@bp.route("/api/highlights")
def api_list_highlights():
    items = db.list_highlights()
    for it in items:
        it["video_url"] = _highlight_url(it["filename"])
    return jsonify(items)


@bp.route("/api/highlights/quota")
def api_highlight_quota():
    """给前端展示存储限制用。"""
    used = _dir_size(HIGHLIGHT_DIR)
    return jsonify({
        "max_mb": HIGHLIGHT_MAX_BYTES // _MB,
        "used_mb": round(used / _MB, 1),
        "cap_mb": HIGHLIGHT_TOTAL_CAP // _MB,
        "free_mb": round(_free_disk(HIGHLIGHT_DIR) / _MB, 1),
        "allowed_exts": sorted(ALLOWED_VIDEO_EXTS),
    })


@bp.route("/api/highlights", methods=["POST"])
def api_create_highlight():
    err = auth.require_login()
    if err:
        return err
    aid, _ = auth.current_user()

    if "video" not in request.files:
        return jsonify({"error": "缺少 video 文件字段"}), 400
    f = request.files["video"]
    if not f.filename:
        return jsonify({"error": "未选择文件"}), 400

    title = (request.form.get("title") or "").strip()
    if not title:
        return jsonify({"error": "标题不能为空"}), 400
    if len(title) > 100:
        return jsonify({"error": "标题最多 100 字"}), 400
    description = (request.form.get("description") or "").strip() or None
    if description and len(description) > 500:
        return jsonify({"error": "描述最多 500 字"}), 400

    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_VIDEO_EXTS:
        return jsonify({"error": f"格式不支持，仅允许 {', '.join(sorted(ALLOWED_VIDEO_EXTS))}"}), 400

    # 文件大小
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    if size == 0:
        return jsonify({"error": "空文件"}), 400
    if size > HIGHLIGHT_MAX_BYTES:
        return jsonify({
            "error": f"文件太大（{size // _MB} MB > 上限 {HIGHLIGHT_MAX_BYTES // _MB} MB）"
        }), 400

    # 目录总量上限
    if _dir_size(HIGHLIGHT_DIR) + size > HIGHLIGHT_TOTAL_CAP:
        return jsonify({
            "error": f"集锦总容量将超过上限 {HIGHLIGHT_TOTAL_CAP // _MB} MB，请联系管理员清理后再传"
        }), 507
    # 磁盘剩余空间保护
    if _free_disk(HIGHLIGHT_DIR) - size < HIGHLIGHT_MIN_FREE:
        return jsonify({"error": "服务器磁盘空间不足，暂时无法上传"}), 507

    HIGHLIGHT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"hl_{uuid.uuid4().hex}.{ext}"
    saved = HIGHLIGHT_DIR / filename
    f.save(str(saved))

    # 非浏览器友好编码（HEVC 等）且服务器有 ffmpeg → 后台转 H.264，先标记 processing
    will_transcode = transcode.has_ffmpeg() and transcode.needs_transcode(saved, ext)
    status = "processing" if will_transcode else "ready"

    hl = db.create_highlight(
        title=title, filename=filename, original_name=f.filename,
        size_bytes=size, description=description, created_by=aid, status=status,
    )
    if will_transcode:
        transcode.submit(hl["id"])
    hl["video_url"] = _highlight_url(hl["filename"])
    return jsonify({"ok": True, "highlight": hl})


@bp.route("/api/highlights/<int:highlight_id>", methods=["DELETE"])
def api_delete_highlight(highlight_id):
    err = auth.require_login()
    if err:
        return err
    aid, _ = auth.current_user()
    filename = db.delete_highlight(highlight_id, aid)
    if not filename:
        return jsonify({"error": "找不到该集锦，或你不是上传者"}), 403
    # 删 DB 记录成功后清理磁盘文件（防穿越：只在 HIGHLIGHT_DIR 内按 basename 删）
    try:
        (HIGHLIGHT_DIR / Path(filename).name).unlink(missing_ok=True)
    except Exception:
        pass
    return jsonify({"ok": True})


@bp.route("/uploads/highlights/<path:filename>")
def serve_highlight(filename):
    # send_from_directory 默认支持 Range 请求，<video> 可拖动进度条
    return send_from_directory(str(HIGHLIGHT_DIR), filename, conditional=True)
