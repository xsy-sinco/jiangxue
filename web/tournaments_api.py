"""赛事板块 API（蓝图）。

报名玩家自助；身价/队伍/拍卖录入/赛程编排需要管理员（拍卖录入也允许"该队队长"）。
选手名字/头像前端从 /api/stats + /api/profiles 按 account_id 映射，这里只管赛事数据。
"""

from __future__ import annotations

import uuid

from flask import Blueprint, jsonify, request, send_from_directory

from web import auth, db
from web import tournaments_db as tdb

bp = Blueprint("tournaments", __name__)

COVER_DIR = db.DB_DIR / "uploads" / "tournaments"
ALLOWED_IMG_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
MAX_COVER_BYTES = 8 * 1024 * 1024  # 8MB


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ============== 封面图上传 ==============

@bp.route("/api/tournaments/cover", methods=["POST"])
def upload_cover():
    """管理员上传赛事封面图，返回可用的 url（存 community/uploads/tournaments/）。"""
    err = auth.require_admin()
    if err:
        return err
    if "image" not in request.files:
        return jsonify({"error": "缺少 image 文件字段"}), 400
    f = request.files["image"]
    if not f.filename:
        return jsonify({"error": "未选择文件"}), 400
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_IMG_EXTS:
        return jsonify({"error": f"格式不支持，仅允许 {', '.join(sorted(ALLOWED_IMG_EXTS))}"}), 400
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    if size == 0:
        return jsonify({"error": "空文件"}), 400
    if size > MAX_COVER_BYTES:
        return jsonify({"error": f"图片太大（{size // 1024 // 1024} MB > {MAX_COVER_BYTES // 1024 // 1024} MB）"}), 400

    COVER_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"cover_{uuid.uuid4().hex}.{ext}"
    f.save(str(COVER_DIR / filename))
    return jsonify({"ok": True, "url": f"/uploads/tournaments/{filename}"})


@bp.route("/uploads/tournaments/<path:filename>")
def serve_cover(filename):
    return send_from_directory(str(COVER_DIR), filename)


# ============== 赛事 ==============

@bp.route("/api/tournaments")
def list_tournaments():
    return jsonify(tdb.list_tournaments())


@bp.route("/api/tournaments/<int:tid>")
def tournament_detail(tid):
    d = tdb.get_detail(tid)
    if not d:
        return jsonify({"error": "找不到该赛事"}), 404
    return jsonify(d)


@bp.route("/api/tournaments", methods=["POST"])
def create_tournament():
    err = auth.require_admin()
    if err:
        return err
    p = request.get_json(silent=True) or {}
    name = (p.get("name") or "").strip()
    if not name:
        return jsonify({"error": "赛事名不能为空"}), 400
    if len(name) > 80:
        return jsonify({"error": "赛事名最多 80 字"}), 400
    budget = _int(p.get("per_team_budget")) or 1000
    aid, _ = auth.current_user()
    t = tdb.create_tournament(
        name=name, description=(p.get("description") or "").strip() or None,
        per_team_budget=budget, created_by=aid,
        cover_url=(p.get("cover_url") or "").strip() or None,
    )
    return jsonify({"ok": True, "tournament": t})


@bp.route("/api/tournaments/<int:tid>", methods=["PATCH"])
def update_tournament(tid):
    err = auth.require_admin()
    if err:
        return err
    if not tdb.get_tournament(tid):
        return jsonify({"error": "找不到该赛事"}), 404
    p = request.get_json(silent=True) or {}
    fields = {}
    if "name" in p:
        fields["name"] = (p.get("name") or "").strip()
    if "description" in p:
        fields["description"] = (p.get("description") or "").strip() or None
    if "per_team_budget" in p:
        fields["per_team_budget"] = _int(p.get("per_team_budget")) or 0
    if "cover_url" in p:
        fields["cover_url"] = (p.get("cover_url") or "").strip() or None
    if "status" in p:
        if p["status"] not in ("registration", "auction", "bracket", "finished"):
            return jsonify({"error": "状态不合法"}), 400
        fields["status"] = p["status"]
    return jsonify({"ok": True, "tournament": tdb.update_tournament(tid, **fields)})


@bp.route("/api/tournaments/<int:tid>", methods=["DELETE"])
def delete_tournament(tid):
    err = auth.require_admin()
    if err:
        return err
    tdb.delete_tournament(tid)
    return jsonify({"ok": True})


# ============== 报名 ==============

@bp.route("/api/tournaments/<int:tid>/signup", methods=["POST"])
def signup(tid):
    err = auth.require_login()
    if err:
        return err
    t = tdb.get_tournament(tid)
    if not t:
        return jsonify({"error": "找不到该赛事"}), 404
    if t["status"] != "registration":
        return jsonify({"error": "报名已截止"}), 400
    aid, _ = auth.current_user()
    tdb.add_signup(tid, aid)
    return jsonify({"ok": True})


@bp.route("/api/tournaments/<int:tid>/signup", methods=["DELETE"])
def withdraw(tid):
    err = auth.require_login()
    if err:
        return err
    t = tdb.get_tournament(tid)
    if not t:
        return jsonify({"error": "找不到该赛事"}), 404
    if t["status"] != "registration":
        return jsonify({"error": "报名已截止，无法退赛，请联系管理员"}), 400
    aid, _ = auth.current_user()
    tdb.remove_signup(tid, aid)
    return jsonify({"ok": True})


@bp.route("/api/tournaments/<int:tid>/signups", methods=["POST"])
def admin_add_signup(tid):
    err = auth.require_admin()
    if err:
        return err
    aid = _int((request.get_json(silent=True) or {}).get("account_id"))
    if aid is None:
        return jsonify({"error": "account_id 必须是数字"}), 400
    tdb.add_signup(tid, aid)
    return jsonify({"ok": True})


@bp.route("/api/tournaments/<int:tid>/signups/<int:account_id>", methods=["DELETE"])
def admin_remove_signup(tid, account_id):
    err = auth.require_admin()
    if err:
        return err
    tdb.remove_signup(tid, account_id)
    return jsonify({"ok": True})


# ============== 身价 / 队长 ==============

@bp.route("/api/tournaments/<int:tid>/valuation", methods=["POST"])
def set_valuation(tid):
    err = auth.require_admin()
    if err:
        return err
    p = request.get_json(silent=True) or {}
    aid = _int(p.get("account_id"))
    if aid is None:
        return jsonify({"error": "account_id 必须是数字"}), 400
    val = p.get("valuation")
    val = _int(val) if val not in (None, "") else None
    if val is not None and val < 0:
        return jsonify({"error": "身价不能为负"}), 400
    tdb.set_valuation(tid, aid, val)
    return jsonify({"ok": True})


@bp.route("/api/tournaments/<int:tid>/captain", methods=["POST"])
def set_captain(tid):
    err = auth.require_admin()
    if err:
        return err
    p = request.get_json(silent=True) or {}
    aid = _int(p.get("account_id"))
    if aid is None:
        return jsonify({"error": "account_id 必须是数字"}), 400
    tdb.set_captain(tid, aid, bool(p.get("is_captain", True)))
    return jsonify({"ok": True})


# ============== 队伍 ==============

@bp.route("/api/tournaments/<int:tid>/teams", methods=["POST"])
def create_team(tid):
    err = auth.require_admin()
    if err:
        return err
    p = request.get_json(silent=True) or {}
    name = (p.get("name") or "").strip()
    if not name:
        return jsonify({"error": "队名不能为空"}), 400
    cap = _int(p.get("captain_account_id"))
    team = tdb.create_team(tid, name, cap, budget=_int(p.get("budget")))
    # 指定的队长：标记 is_captain + 归入本队
    if cap is not None and tdb.get_signup(tid, cap):
        tdb.set_captain(tid, cap, True)
        tdb.assign_player(tid, cap, team["id"], 0)
    return jsonify({"ok": True, "team": team})


@bp.route("/api/tournaments/<int:tid>/teams/<int:team_id>", methods=["PATCH"])
def update_team(tid, team_id):
    err = auth.require_admin()
    if err:
        return err
    p = request.get_json(silent=True) or {}
    tdb.update_team(team_id, name=(p.get("name") or None),
                    captain_account_id=_int(p.get("captain_account_id")),
                    budget=_int(p.get("budget")))
    return jsonify({"ok": True})


@bp.route("/api/tournaments/<int:tid>/teams/<int:team_id>", methods=["DELETE"])
def delete_team(tid, team_id):
    err = auth.require_admin()
    if err:
        return err
    tdb.delete_team(team_id)
    return jsonify({"ok": True})


# ============== 拍卖录入 ==============

@bp.route("/api/tournaments/<int:tid>/assign", methods=["POST"])
def assign(tid):
    """录入成交：把选手以某价归入某队。管理员或该队队长可操作。"""
    err = auth.require_login()
    if err:
        return err
    aid, _ = auth.current_user()
    p = request.get_json(silent=True) or {}
    target = _int(p.get("account_id"))
    team_id = _int(p.get("team_id"))
    price = _int(p.get("price"))
    if target is None or team_id is None or price is None:
        return jsonify({"error": "account_id / team_id / price 必须是数字"}), 400
    if price < 0:
        return jsonify({"error": "成交价不能为负"}), 400

    team = tdb.get_team(team_id)
    if not team or team["tournament_id"] != tid:
        return jsonify({"error": "队伍不存在"}), 404
    # 权限：管理员 或 该队队长本人
    if not auth.is_admin(aid) and team.get("captain_account_id") != aid:
        return jsonify({"error": "只有管理员或该队队长能录入"}), 403

    su = tdb.get_signup(tid, target)
    if not su:
        return jsonify({"error": "该选手未报名本赛事"}), 400
    if su.get("is_captain"):
        return jsonify({"error": "队长不参与拍卖"}), 400
    if su.get("team_id"):
        return jsonify({"error": "该选手已在某队，请先撤销再重拍"}), 400

    # 允许超出预算（剩余可为负）——拍卖时管理员自行把控
    tdb.assign_player(tid, target, team_id, price)
    return jsonify({"ok": True})


@bp.route("/api/tournaments/<int:tid>/unassign", methods=["POST"])
def unassign(tid):
    err = auth.require_admin()
    if err:
        return err
    target = _int((request.get_json(silent=True) or {}).get("account_id"))
    if target is None:
        return jsonify({"error": "account_id 必须是数字"}), 400
    tdb.unassign_player(tid, target)
    return jsonify({"ok": True})


# ============== 赛程节点 ==============

@bp.route("/api/tournaments/<int:tid>/matches", methods=["POST"])
def create_match(tid):
    err = auth.require_admin()
    if err:
        return err
    p = request.get_json(silent=True) or {}
    m = tdb.create_match(
        tid,
        round_name=(p.get("round_name") or "").strip(),
        order_idx=_int(p.get("order_idx")) or 0,
        team_a_id=_int(p.get("team_a_id")),
        team_b_id=_int(p.get("team_b_id")),
        best_of=_int(p.get("best_of")) or 1,
        scheduled_time=(p.get("scheduled_time") or "").strip() or None,
    )
    return jsonify({"ok": True, "match": m})


@bp.route("/api/tournaments/<int:tid>/matches/<int:mid>", methods=["PATCH"])
def update_match(tid, mid):
    err = auth.require_admin()
    if err:
        return err
    p = request.get_json(silent=True) or {}
    fields = {}
    for k in ("round_name", "scheduled_time"):
        if k in p:
            fields[k] = (p.get(k) or "").strip() or None
    for k in ("order_idx", "team_a_id", "team_b_id", "score_a", "score_b", "winner_team_id", "best_of"):
        if k in p:
            fields[k] = _int(p.get(k))
    tdb.update_match(mid, **fields)
    return jsonify({"ok": True})


@bp.route("/api/tournaments/<int:tid>/matches/<int:mid>", methods=["DELETE"])
def delete_match(tid, mid):
    err = auth.require_admin()
    if err:
        return err
    tdb.delete_match(mid)
    return jsonify({"ok": True})
