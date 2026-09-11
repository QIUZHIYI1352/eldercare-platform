"""站内消息：教师发起一对一对话，学员仅可回复；教师可发私密备注（kind=note）"""
from fastapi import APIRouter, Depends, HTTPException

from backend import auth
from backend import database as db
from .auth_router import current_user
from .tasks import TEACHER_ROLES, STUDENT_ROLES

router = APIRouter(prefix="/api/messages", tags=["messages"])


def _is_teacher(user):
    return user["role"] in TEACHER_ROLES


def _pair_rows(me_id, other_id):
    return db.query(
        "SELECT * FROM messages WHERE (from_user_id=? AND to_user_id=?) "
        "OR (from_user_id=? AND to_user_id=?) ORDER BY created_at",
        (me_id, other_id, other_id, me_id),
    )


def _user_brief(uid):
    row = db.query_one("SELECT id, name, username, role FROM users WHERE id = ?", (uid,))
    if not row:
        return None
    return {"id": row["id"], "name": row["name"], "username": row["username"],
            "role": row["role"]}


@router.get("/conversations")
def conversations(user=Depends(current_user)):
    rows = db.query(
        "SELECT * FROM messages WHERE from_user_id=? OR to_user_id=? "
        "ORDER BY created_at DESC LIMIT 500",
        (user["id"], user["id"]),
    )
    is_teacher = _is_teacher(user)
    seen, out = set(), []
    for m in rows:
        if not is_teacher and m["kind"] == "note":
            continue
        other = m["to_user_id"] if m["from_user_id"] == user["id"] else m["from_user_id"]
        if other in seen:
            continue
        seen.add(other)
        brief = _user_brief(other)
        if not brief:
            continue
        brief["last_body"] = m["body"]
        brief["last_at"] = m["created_at"]
        brief["last_kind"] = m["kind"]
        brief["unread"] = db.query_one(
            "SELECT COUNT(*) AS c FROM messages WHERE from_user_id=? AND to_user_id=? "
            "AND read_at IS NULL AND kind <> 'note'", (other, user["id"]))["c"]
        out.append(brief)
    return {"items": out}


@router.get("/unread-count")
def unread_count(user=Depends(current_user)):
    row = db.query_one(
        "SELECT COUNT(*) AS c FROM messages WHERE to_user_id=? AND read_at IS NULL "
        "AND kind <> 'note'", (user["id"],))
    return {"count": row["c"] if row else 0}


@router.get("/thread/{uid}")
def thread(uid: str, user=Depends(current_user)):
    brief = _user_brief(uid)
    if not brief:
        raise HTTPException(status_code=404, detail="用户不存在")
    rows = _pair_rows(user["id"], uid)
    if not _is_teacher(user):
        rows = [m for m in rows if m["kind"] != "note"]
    for m in rows:
        if m["to_user_id"] == user["id"] and not m["read_at"]:
            db.execute("UPDATE messages SET read_at=? WHERE id=?", (db.now(), m["id"]))
            m["read_at"] = db.now()
    return {"user": brief, "items": rows}


@router.post("")
def send(payload: dict, user=Depends(current_user)):
    to_user_id = payload.get("to_user_id") or ""
    body = (payload.get("body") or "").strip()
    kind = payload.get("kind") or "message"
    if not to_user_id or not body:
        raise HTTPException(status_code=400, detail="请填写接收人与消息内容")
    if len(body) > 1000:
        raise HTTPException(status_code=400, detail="消息内容过长（上限 1000 字）")
    target = _user_brief(to_user_id)
    if not target:
        raise HTTPException(status_code=404, detail="接收人不存在")
    if _is_teacher(user):
        if target["role"] not in STUDENT_ROLES:
            raise HTTPException(status_code=400, detail="只能向学员发送消息")
        if kind not in ("message", "note"):
            raise HTTPException(status_code=400, detail="非法消息类型")
        if user["role"] != "admin":
            mine = db.query_one(
                "SELECT 1 AS x FROM class_members m JOIN classes c ON m.class_id=c.id "
                "WHERE m.user_id=? AND c.owner_user_id=?", (to_user_id, user["id"]))
            if not mine:
                raise HTTPException(status_code=403, detail="该学员不在你的班级中")
    else:
        if target["role"] not in TEACHER_ROLES:
            raise HTTPException(status_code=400, detail="只能回复教师")
        exists = db.query_one(
            "SELECT 1 AS x FROM messages WHERE (from_user_id=? AND to_user_id=?) "
            "OR (from_user_id=? AND to_user_id=?)",
            (user["id"], to_user_id, to_user_id, user["id"]))
        if not exists:
            raise HTTPException(status_code=403, detail="暂无可回复的对话，请等待教师联系")
        kind = "message"
    mid = db.gen_id("msg")
    db.execute(
        "INSERT INTO messages (id, from_user_id, to_user_id, body, kind, "
        "related_record_id, read_at, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (mid, user["id"], to_user_id, body, kind,
         payload.get("related_record_id") or "", None, db.now()),
    )
    if kind == "message":
        auth.audit(user, "send_message", f"发送消息给 {target['username']}")
    return {"id": mid}
