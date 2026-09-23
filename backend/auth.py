"""认证与安全：口令哈希、令牌、权限、审计日志

口令哈希采用 PBKDF2-HMAC-SHA256（加盐 + 多轮迭代），
并兼容历史上单轮 SHA-256 的旧哈希：校验通过后会自动升级为新格式。
"""
import hashlib
import hmac
import os
import secrets

from backend import database as db

# 迭代次数：可用环境变量覆盖，默认 20 万次（OWASP 对 PBKDF2-SHA256 的建议下限）
PBKDF2_ITERATIONS = int(os.environ.get("PASSWORD_PBKDF2_ITERATIONS", "200000"))
# 会话有效期（秒）。默认 7 天；设为 0 或负数表示永不过期。
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", str(7 * 24 * 3600)))
# 续期阈值：会话存活超过 TTL 的一半时，每次访问自动续期（活跃用户不掉线）
_SESSION_RENEW_AFTER = SESSION_TTL_SECONDS // 2 if SESSION_TTL_SECONDS > 0 else 0

_SCHEME = "pbkdf2_sha256"


def _pbkdf2(password: str, salt: str, iterations: int) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             salt.encode("utf-8"), iterations)
    return dk.hex()


def hash_password(password: str, salt: str = "") -> str:
    """生成口令哈希，格式：pbkdf2_sha256$迭代次数$盐$摘要"""
    if not salt:
        salt = secrets.token_hex(16)
    return "%s$%d$%s$%s" % (_SCHEME, PBKDF2_ITERATIONS, salt,
                            _pbkdf2(password, salt, PBKDF2_ITERATIONS))


def _verify_legacy(password: str, stored: str) -> bool:
    """旧格式（salt$sha256(salt+password)，单轮）校验，仅用于兼容历史数据。"""
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    expect = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return hmac.compare_digest(expect, digest)


def verify_password(password: str, stored: str) -> bool:
    """常量时间比较，避免时序侧信道。"""
    if not password or not stored:
        return False
    if stored.startswith(_SCHEME + "$"):
        try:
            _, iters, salt, digest = stored.split("$", 3)
            return hmac.compare_digest(
                _pbkdf2(password, salt, int(iters)), digest)
        except (ValueError, TypeError):
            return False
    return _verify_legacy(password, stored)


def needs_rehash(stored: str) -> bool:
    """旧格式或迭代次数低于当前配置时需要重新哈希。"""
    if not stored or not stored.startswith(_SCHEME + "$"):
        return True
    try:
        _, iters, _, _ = stored.split("$", 3)
        return int(iters) < PBKDF2_ITERATIONS
    except (ValueError, TypeError):
        return True


# ---------- 会话 ----------

def create_session(user_id: str) -> str:
    token = secrets.token_hex(24)
    db.execute(
        "INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)",
        (token, user_id, db.now()),
    )
    _cleanup_expired_sessions()
    return token


def get_user_by_token(token: str):
    if not token:
        return None
    row = db.query_one(
        "SELECT s.created_at AS session_created_at, u.* FROM sessions s "
        "JOIN users u ON s.user_id = u.id WHERE s.token = ?",
        (token,),
    )
    if not row:
        return None
    created_at = int(row.pop("session_created_at", 0) or 0)
    if SESSION_TTL_SECONDS > 0:
        age = db.now() - created_at
        if age > SESSION_TTL_SECONDS:
            destroy_session(token)
            return None
        # 活跃会话自动续期，避免长时间操作中途被登出
        if age > _SESSION_RENEW_AFTER:
            db.execute("UPDATE sessions SET created_at=? WHERE token=?",
                       (db.now(), token))
    row.pop("password_hash", None)
    return row


def destroy_session(token: str):
    if token:
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))


def _cleanup_expired_sessions():
    """顺带清理过期会话，避免 sessions 表无限增长。"""
    if SESSION_TTL_SECONDS > 0:
        try:
            db.execute("DELETE FROM sessions WHERE created_at < ?",
                       (db.now() - SESSION_TTL_SECONDS,))
        except Exception:
            pass


class PermissionDenied(PermissionError):
    """权限不足。

    继承 PermissionError 以兼容既有调用方，同时便于 FastAPI 在 main.py
    中注册专门的异常处理器把状态码映射为 403（否则会退化成 500）。
    """


def require_role(user, roles):
    """校验当前用户角色是否在允许列表中。user 为 None 或角色不符抛异常。"""
    if not user:
        raise PermissionDenied("未登录或会话已过期")
    if roles and user["role"] not in roles:
        raise PermissionDenied("无权限执行该操作")


def audit(user, action: str, detail: str = ""):
    if not user:
        return
    db.execute(
        "INSERT INTO audit_logs (id, user_id, username, action, detail, created_at) VALUES (?,?,?,?,?,?)",
        (db.gen_id("audit"), user.get("id", ""), user.get("username", ""),
         action, detail, db.now()),
    )


def mask_info(text: str) -> str:
    """敏感信息脱敏：手机号保留前 3 后 4，姓名保留首尾字。"""
    if not text:
        return text
    text = str(text)
    if len(text) <= 1:
        return "*"
    if len(text) == 2:
        return text[0] + "*"
    if text.isdigit() and len(text) == 11:
        return text[:3] + "****" + text[-4:]
    return text[0] + "*" * (len(text) - 2) + text[-1]
