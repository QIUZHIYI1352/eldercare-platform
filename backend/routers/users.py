"""用户管理接口"""
from fastapi import APIRouter, Depends, HTTPException

from backend import auth
from backend import database as db
from config import ROLES
from .auth_router import current_user

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("")
def list_users(user=Depends(current_user)):
    auth.require_role(user, ["admin"])
    rows = db.query("SELECT id, username, name, role, organization, phone, created_at FROM users ORDER BY created_at")
    # 隐私脱敏：非管理员看不到明文手机号/姓名（这里仅管理员可看，按需脱敏）
    for r in rows:
        r["phone_masked"] = auth.mask_info(r.get("phone") or "")
    return {"items": rows}


@router.post("")
def create_user(payload: dict, user=Depends(current_user)):
    auth.require_role(user, ["admin"])
    username = (payload.get("username") or "").strip()
    if not username or not payload.get("password"):
        raise HTTPException(status_code=400, detail="用户名与密码必填")
    if db.query_one("SELECT id FROM users WHERE username = ?", (username,)):
        raise HTTPException(status_code=409, detail="用户名已存在")
    role = payload.get("role") or "nursing_student"
    if role not in ROLES:
        raise HTTPException(status_code=400, detail="非法角色")
    uid = db.gen_id("user")
    db.execute(
        "INSERT INTO users (id, username, password_hash, name, role, organization, phone, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (uid, username, auth.hash_password(payload.get("password")),
         payload.get("name") or username, role,
         payload.get("organization") or "", payload.get("phone") or "", db.now()),
    )
    auth.audit(user, "create_user", f"创建用户 {username}({ROLES[role]})")
    return {"id": uid}


@router.delete("/{uid}")
def delete_user(uid: str, user=Depends(current_user)):
    auth.require_role(user, ["admin"])
    db.execute("DELETE FROM users WHERE id = ?", (uid,))
    auth.audit(user, "delete_user", f"删除用户 {uid}")
    return {"ok": True}
