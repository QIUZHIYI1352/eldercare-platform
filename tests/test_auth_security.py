"""认证与安全相关纯逻辑测试（不依赖数据库）。"""
import hashlib
import time

from backend import auth, ratelimit


def test_password_hash_roundtrip():
    stored = auth.hash_password("s3cret-密码")
    assert stored.startswith("pbkdf2_sha256$")
    assert auth.verify_password("s3cret-密码", stored)
    assert not auth.verify_password("wrong", stored)


def test_password_salt_is_random():
    a = auth.hash_password("same-password")
    b = auth.hash_password("same-password")
    assert a != b, "相同口令必须因随机盐得到不同哈希"
    assert auth.verify_password("same-password", a)
    assert auth.verify_password("same-password", b)


def test_verify_legacy_sha256_hash():
    """历史数据是 salt$sha256(salt+password) 单轮格式，必须仍能登录。"""
    salt = "abcd1234abcd1234"
    legacy = salt + "$" + hashlib.sha256((salt + "oldpwd").encode()).hexdigest()
    assert auth.verify_password("oldpwd", legacy)
    assert not auth.verify_password("nope", legacy)


def test_needs_rehash():
    assert auth.needs_rehash("") is True
    salt = "abcd1234abcd1234"
    legacy = salt + "$" + hashlib.sha256((salt + "x").encode()).hexdigest()
    assert auth.needs_rehash(legacy) is True, "旧格式应触发升级"
    assert auth.needs_rehash(auth.hash_password("x")) is False
    # 迭代次数被人为调低时也应升级
    weak = "pbkdf2_sha256$1000$salt$deadbeef"
    assert auth.needs_rehash(weak) is True


def test_verify_rejects_garbage():
    for bad in ["", "noseparator", "pbkdf2_sha256$bad$salt$hash", None]:
        assert auth.verify_password("x", bad) is False


def test_password_is_not_plaintext_in_hash():
    stored = auth.hash_password("my-plain-password")
    assert "my-plain-password" not in stored


def test_mask_info():
    assert auth.mask_info("张三") == "张*"
    assert auth.mask_info("欧阳小明") == "欧**明"
    assert auth.mask_info("13800000000") == "138****0000"
    assert auth.mask_info("A") == "*"
    assert auth.mask_info("") == ""


def test_ratelimit_window():
    ratelimit.reset()
    key = "unit:test"
    assert all(ratelimit.allow(key, limit=3, window=60) for _ in range(3))
    assert ratelimit.allow(key, limit=3, window=60) is False, "超额必须被拒绝"
    assert ratelimit.retry_after(key, 60) > 0
    ratelimit.reset(key)
    assert ratelimit.allow(key, limit=3, window=60) is True


def test_ratelimit_expires():
    ratelimit.reset()
    key = "unit:expire"
    assert ratelimit.allow(key, limit=1, window=0) is True
    # window=0 表示不保留历史，应当永远放行
    assert ratelimit.allow(key, limit=1, window=0) is True


def test_session_ttl_expires_old_session():
    """TTL 应当让过期会话失效，并顺带删除该记录。"""
    from backend import database as db

    captured = {}

    class FakeDb:
        @staticmethod
        def query_one(sql, params=()):
            return {"session_created_at": captured["created"], "id": "u1",
                    "username": "u", "role": "admin", "password_hash": "x"}

        @staticmethod
        def execute(sql, params=()):
            captured["deleted"] = sql

        @staticmethod
        def now():
            return captured["now"]

    originals = {k: getattr(db, k) for k in ("query_one", "execute", "now")}
    db.query_one, db.execute, db.now = FakeDb.query_one, FakeDb.execute, FakeDb.now
    try:
        auth.SESSION_TTL_SECONDS = 100
        captured["now"] = 1000
        captured["created"] = 1000 - 500  # 已远超 TTL
        assert auth.get_user_by_token("t") is None
        assert "DELETE FROM sessions" in captured["deleted"]

        captured["created"] = 1000 - 10  # 仍在有效期内
        assert auth.get_user_by_token("t") is not None
    finally:
        auth.SESSION_TTL_SECONDS = 7 * 24 * 3600
        db.query_one, db.execute, db.now = (originals["query_one"],
                                            originals["execute"], originals["now"])


def test_require_role():
    import pytest
    assert auth.require_role({"role": "admin"}, ["admin"]) is None
    with pytest.raises(PermissionError):
        auth.require_role(None, ["admin"])
    with pytest.raises(PermissionError):
        auth.require_role({"role": "nursing_student"}, ["admin"])


def test_hash_is_slow_enough_to_resist_bruteforce():
    """PBKDF2 迭代次数应足够高（防止有人调低后无感知）。"""
    assert auth.PBKDF2_ITERATIONS >= 100_000
    start = time.time()
    stored = auth.hash_password("timing-check")
    elapsed = time.time() - start
    assert auth.verify_password("timing-check", stored)
    assert elapsed < 5.0, "单次哈希不应慢到影响可用性"
