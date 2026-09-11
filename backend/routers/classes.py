"""班级管理：班级 CRUD + 学员按账号加入/移除"""
from fastapi import APIRouter, Depends, HTTPException

from backend import auth
from backend import database as db
from .auth_router import current_user
from .tasks import TEACHER_ROLES, STUDENT_ROLES, can_manage

router = APIRouter(prefix="/api/classes", tags=["classes"])


def _own_scope(user):
    return "" if user["role"] == "admin" else "WHERE owner_user_id = ?"


@router.get("")
def list_classes(user=Depends(current_user)):
    if user["role"] not in TEACHER_ROLES:
        raise HTTPException(status_code=403, detail="无权限")
    where = _own_scope(user)
    rows = db.query(
        "SELECT c.id, c.name, c.description, c.created_at, "
        "(SELECT COUNT(*) FROM class_members m WHERE m.class_id = c.id) AS member_count "
        "FROM classes c " + where + " ORDER BY c.created_at DESC",
        (user["id"],) if where else (),
    )
    return {"items": rows}


@router.post("")
def create_class(payload: dict, user=Depends(current_user)):
    if user["role"] not in TEACHER_ROLES:
        raise HTTPException(status_code=403, detail="无权限")
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="班级名称必填")
    cid = db.gen_id("cls")
    db.execute("INSERT INTO classes (id, name, owner_user_id, description, created_at) VALUES (?,?,?,?,?)",
               (cid, name, user["id"], payload.get("description") or "", db.now()))
    auth.audit(user, "create_class", f"创建班级 {name}")
    return {"id": cid}


def _get_class(cid, user):
    row = db.query_one("SELECT * FROM classes WHERE id = ?", (cid,))
    if not row:
        raise HTTPException(status_code=404, detail="班级不存在")
    if not can_manage(user, row["owner_user_id"]):
        raise HTTPException(status_code=403, detail="无权限")
    return row


@router.put("/{cid}")
def update_class(cid: str, payload: dict, user=Depends(current_user)):
    row = _get_class(cid, user)
    db.execute("UPDATE classes SET name=?, description=? WHERE id=?",
               ((payload.get("name") or row["name"]).strip(),
                payload.get("description") or row["description"], cid))
    auth.audit(user, "update_class", f"更新班级 {row['name']}")
    return {"ok": True}


@router.delete("/{cid}")
def delete_class(cid: str, user=Depends(current_user)):
    row = _get_class(cid, user)
    db.execute("DELETE FROM classes WHERE id = ?", (cid,))
    db.execute("DELETE FROM class_members WHERE class_id = ?", (cid,))
    auth.audit(user, "delete_class", f"删除班级 {row['name']}")
    return {"ok": True}


@router.get("/{cid}/members")
def list_members(cid: str, user=Depends(current_user)):
    _get_class(cid, user)
    rows = db.query(
        "SELECT u.id, u.username, u.name, u.role, u.organization FROM class_members m "
        "JOIN users u ON m.user_id = u.id WHERE m.class_id = ? ORDER BY u.created_at",
        (cid,),
    )
    return {"items": rows}


@router.post("/{cid}/members")
def add_members(cid: str, payload: dict, user=Depends(current_user)):
    _get_class(cid, user)
    usernames = [(u or "").strip() for u in payload.get("usernames") or []]
    usernames = [u for u in usernames if u]
    added, errors = [], []
    for uname in usernames:
        row = db.query_one("SELECT id, role FROM users WHERE username = ?", (uname,))
        if not row:
            errors.append(f"账号不存在: {uname}")
            continue
        if row["role"] not in STUDENT_ROLES:
            errors.append(f"非学员账号: {uname}")
            continue
        exists = db.query_one("SELECT 1 AS x FROM class_members WHERE class_id=? AND user_id=?",
                              (cid, row["id"]))
        if exists:
            continue
        db.execute("INSERT INTO class_members (class_id, user_id, joined_at) VALUES (?,?,?)",
                   (cid, row["id"], db.now()))
        added.append(uname)
    auth.audit(user, "add_class_members", f"班级 {cid} 加入学员 {len(added)} 人")
    return {"added": added, "errors": errors}


@router.delete("/{cid}/members/{uid}")
def remove_member(cid: str, uid: str, user=Depends(current_user)):
    _get_class(cid, user)
    db.execute("DELETE FROM class_members WHERE class_id=? AND user_id=?", (cid, uid))
    return {"ok": True}
