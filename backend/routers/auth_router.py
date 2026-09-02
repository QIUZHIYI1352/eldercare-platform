"""认证相关接口"""
from fastapi import APIRouter, Header, HTTPException

from backend import auth
from backend import database as db
from config import ROLES

router = APIRouter(prefix="/api/auth", tags=["auth"])


def current_user(authorization: str = Header(default="")):
    token = ""
    if authorization.startswith("Bearer "):
        token = authorization[7:]
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    user = auth.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="会话无效或已过期")
    return user


@router.post("/login")
def login(payload: dict):
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    user = db.query_one("SELECT * FROM users WHERE username = ?", (username,))
    if not user or not auth.verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = auth.create_session(user["id"])
    user.pop("password_hash", None)
    auth.audit(user, "login", f"用户 {username} 登录系统")
    return {"token": token, "user": user}


@router.post("/register")
def register(payload: dict):
    """开放注册：面向护理大学生/长期护理员/养老护理员/养老服务师自助开户。"""
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    name = (payload.get("name") or "").strip() or username
    role = payload.get("role") or "elderly_caregiver"
    if not username or len(username) < 3:
        raise HTTPException(status_code=400, detail="用户名至少 3 位")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    # 仅允许注册业务角色，禁止自助注册管理员
    allowed = [r for r in ROLES if r != "admin"]
    if role not in allowed:
        raise HTTPException(status_code=400, detail="非法角色")
    if db.query_one("SELECT id FROM users WHERE username = ?", (username,)):
        raise HTTPException(status_code=409, detail="用户名已存在")
    uid = db.gen_id("user")
    db.execute(
        "INSERT INTO users (id, username, password_hash, name, role, organization, phone, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (uid, username, auth.hash_password(password), name, role,
         payload.get("organization") or "", payload.get("phone") or "", db.now()),
    )
    user = db.query_one("SELECT id, username, name, role, organization, phone, created_at FROM users WHERE id = ?", (uid,))
    token = auth.create_session(uid)
    auth.audit(user, "register", f"新用户注册 {username}({ROLES[role]})")
    return {"token": token, "user": user}


@router.post("/logout")
def logout(user=__import__("fastapi").Depends(current_user),
           authorization: str = Header(default="")):
    token = authorization[7:] if authorization.startswith("Bearer ") else ""
    auth.destroy_session(token)
    return {"ok": True}


@router.get("/me")
def me(user=__import__("fastapi").Depends(current_user)):
    return {"user": user}
