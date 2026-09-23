"""认证相关接口"""
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from backend import auth
from backend import ratelimit
from backend import database as db
from config import ROLES

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 限流参数
LOGIN_LIMIT, LOGIN_WINDOW = 10, 300      # 同一 IP+账号：5 分钟内最多 10 次
REGISTER_LIMIT, REGISTER_WINDOW = 8, 3600  # 同一 IP：1 小时内最多注册 8 个账号


def _client_ip(request: Request) -> str:
    return (request.client.host if request.client else "") or "unknown"


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
def login(payload: dict, request: Request):
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    ip = _client_ip(request)

    key = f"login:{ip}:{username}"
    if not ratelimit.allow(key, LOGIN_LIMIT, LOGIN_WINDOW):
        raise HTTPException(
            status_code=429,
            detail=f"登录尝试过于频繁，请 {ratelimit.retry_after(key, LOGIN_WINDOW) // 60 + 1} 分钟后再试")

    user = db.query_one("SELECT * FROM users WHERE username = ?", (username,))
    if not user or not auth.verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # 旧哈希（单轮 SHA-256）在登录成功时静默升级为 PBKDF2
    if auth.needs_rehash(user["password_hash"]):
        try:
            db.execute("UPDATE users SET password_hash=? WHERE id=?",
                       (auth.hash_password(password), user["id"]))
        except Exception:
            pass

    ratelimit.reset(key)
    token = auth.create_session(user["id"])
    user.pop("password_hash", None)
    auth.audit(user, "login", f"用户 {username} 登录系统")
    return {"token": token, "user": user}


@router.post("/register")
def register(payload: dict, request: Request):
    """开放注册：面向护理大学生/长期护理员/养老护理员/养老服务师自助开户。"""
    ip = _client_ip(request)
    if not ratelimit.allow(f"register:{ip}", REGISTER_LIMIT, REGISTER_WINDOW):
        raise HTTPException(status_code=429, detail="注册过于频繁，请稍后再试")

    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    name = (payload.get("name") or "").strip() or username
    role = payload.get("role") or "elderly_caregiver"
    # 字段长度限制：users.username 为 VARCHAR(64)，超长会触发数据库异常
    if not username or len(username) < 3:
        raise HTTPException(status_code=400, detail="用户名至少 3 位")
    if len(username) > 64:
        raise HTTPException(status_code=400, detail="用户名最长 64 位")
    if any(ch.isspace() for ch in username):
        raise HTTPException(status_code=400, detail="用户名不能包含空格")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    if len(payload.get("phone") or "") > 32:
        raise HTTPException(status_code=400, detail="手机号过长")
    # 仅允许注册业务角色，禁止自助注册管理员
    allowed = [r for r in ROLES if r != "admin"]
    if role not in allowed:
        raise HTTPException(status_code=400, detail="非法角色")
    if db.query_one("SELECT id FROM users WHERE username = ?", (username,)):
        raise HTTPException(status_code=409, detail="用户名已存在")
    uid = db.gen_id("user")
    db.execute(
        "INSERT INTO users (id, username, password_hash, name, role, organization, phone, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (uid, username, auth.hash_password(password), name[:64], role,
         (payload.get("organization") or "")[:128], (payload.get("phone") or "")[:32], db.now()),
    )
    user = db.query_one("SELECT id, username, name, role, organization, phone, created_at FROM users WHERE id = ?", (uid,))
    token = auth.create_session(uid)
    auth.audit(user, "register", f"新用户注册 {username}({ROLES[role]})")
    return {"token": token, "user": user}


@router.post("/logout")
def logout(user=Depends(current_user),
           authorization: str = Header(default="")):
    token = authorization[7:] if authorization.startswith("Bearer ") else ""
    auth.destroy_session(token)
    return {"ok": True}


@router.get("/me")
def me(user=Depends(current_user)):
    return {"user": user}
