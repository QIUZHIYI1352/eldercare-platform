"""认证与安全：口令哈希、令牌、权限、审计日志"""
import hashlib
import secrets

from backend import database as db


def hash_password(password: str, salt: str = "") -> str:
    if not salt:
        salt = secrets.token_hex(8)
    return salt + "$" + hashlib.sha256((salt + password).encode()).hexdigest()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
        return hash_password(password, salt) == stored
    except Exception:
        return False


def create_session(user_id: str) -> str:
    token = secrets.token_hex(24)
    db.execute(
        "INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)",
        (token, user_id, db.now()),
    )
    return token


def get_user_by_token(token: str):
    row = db.query_one(
        "SELECT u.* FROM sessions s JOIN users u ON s.user_id = u.id WHERE s.token = ?",
        (token,),
    )
    if not row:
        return None
    row.pop("password_hash", None)
    return row


def destroy_session(token: str):
    db.execute("DELETE FROM sessions WHERE token = ?", (token,))


def require_role(user, roles):
    """校验当前用户角色是否在允许列表中。user 为 None 或角色不符抛异常。"""
    if not user:
        raise PermissionError("未登录或会话已过期")
    if roles and user["role"] not in roles:
        raise PermissionError("无权限执行该操作")


def audit(user, action: str, detail: str = ""):
    if not user:
        return
    db.execute(
        "INSERT INTO audit_logs (id, user_id, username, action, detail, created_at) VALUES (?,?,?,?,?,?)",
        (db.gen_id("audit"), user.get("id", ""), user.get("username", ""),
         action, detail, db.now()),
    )


def mask_info(text: str) -> str:
    """敏感信息脱敏：姓名、手机号"""
    if not text:
        return text
    if len(text) <= 1:
        return "*"
    if len(text) == 2:
        return text[0] + "*"
    # 手机号
    if text.isdigit() and len(text) == 11:
        return text[:3] + "****" + text[-4:]
    return text[0] + "*" * (len(text) - 2) + text[-1]
