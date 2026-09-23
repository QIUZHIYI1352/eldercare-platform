"""测试夹具。

生产代码的数据层是 MySQL（backend/database.py 用 PyMySQL，并把 ? 转 %s）。
为了让接口能在没有 MySQL 的环境里端到端跑起来，这里不改生产代码，
而是在测试期用 sqlite3 实现同一组函数（query / query_one / execute / init_db /
gen_id / now / json_dump），并把少数 MySQL 专有语法翻译成 SQLite 写法。
"""
import json
import re
import sqlite3
import time
import uuid

import pytest

# 与 backend/database.py::_DDL 同构的 SQLite 建表语句
_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
    name TEXT NOT NULL, role TEXT NOT NULL, organization TEXT, phone TEXT, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS processes (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT, description TEXT,
    steps TEXT, created_by TEXT, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT, description TEXT,
    conditions TEXT, duration REAL DEFAULT 1.0, sample_ref TEXT,
    template_type TEXT, template_data TEXT, created_by TEXT, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT, url TEXT, location TEXT,
    status TEXT, privacy_mask INTEGER DEFAULT 1, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS monitoring_records (
    id TEXT PRIMARY KEY, device_id TEXT, process_id TEXT, user_id TEXT,
    started_at INTEGER, ended_at INTEGER, detected_actions TEXT, missed_steps TEXT,
    completed_steps TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS training_records (
    id TEXT PRIMARY KEY, user_id TEXT, process_id TEXT, task_id TEXT,
    capture_record_id TEXT, score REAL DEFAULT 0, completed_steps TEXT,
    missed_steps TEXT, duration INTEGER DEFAULT 0, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS privacy_settings (
    "key" TEXT PRIMARY KEY, value TEXT, updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY, user_id TEXT, username TEXT, action TEXT, detail TEXT,
    created_at INTEGER
);
CREATE TABLE IF NOT EXISTS motion_capture_records (
    id TEXT PRIMARY KEY, user_id TEXT, task_id TEXT, process_id TEXT, source TEXT,
    source_type TEXT, status TEXT, started_at INTEGER, ended_at INTEGER,
    steps TEXT, segments TEXT, frames TEXT, score REAL, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS classes (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, owner_user_id TEXT NOT NULL,
    description TEXT, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, from_user_id TEXT NOT NULL, to_user_id TEXT NOT NULL,
    body TEXT, kind TEXT, related_record_id TEXT, read_at INTEGER, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS class_members (
    class_id TEXT NOT NULL, user_id TEXT NOT NULL, joined_at INTEGER,
    PRIMARY KEY (class_id, user_id)
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, class_id TEXT NOT NULL, process_id TEXT NOT NULL,
    title TEXT NOT NULL, description TEXT, deadline INTEGER, status TEXT,
    created_by TEXT, created_at INTEGER
);
"""


class SqliteShim:
    """实现 backend.database 的公开函数，落到内存 SQLite。"""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_DDL)

    @staticmethod
    def translate(sql):
        # 反引号标识符（MySQL 风格）-> 双引号（SQLite 风格）
        s = sql.replace("`", '"')
        # INSERT ... ON DUPLICATE KEY UPDATE -> INSERT OR REPLACE
        if "ON DUPLICATE KEY UPDATE" in s.upper():
            s = re.sub(r"ON\s+DUPLICATE\s+KEY\s+UPDATE.*", "", s, flags=re.S | re.I)
            s = re.sub(r"^\s*INSERT\s+INTO", "INSERT OR REPLACE INTO", s, flags=re.I)
        return s.strip()

    def query(self, sql, params=()):
        cur = self.conn.execute(self.translate(sql), tuple(params or ()))
        return [dict(r) for r in cur.fetchall()]

    def query_one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql, params=()):
        self.conn.execute(self.translate(sql), tuple(params or ()))
        self.conn.commit()


@pytest.fixture()
def shim(monkeypatch):
    """把数据层换成 SQLite 垫片，返回垫片对象。"""
    from backend import database

    s = SqliteShim()
    monkeypatch.setattr(database, "init_db", lambda: None)  # 表已由垫片建好
    monkeypatch.setattr(database, "query", s.query)
    monkeypatch.setattr(database, "query_one", s.query_one)
    monkeypatch.setattr(database, "execute", s.execute)
    monkeypatch.setattr(database, "now", lambda: int(time.time()))
    monkeypatch.setattr(
        database, "gen_id", lambda prefix="id": f"{prefix}_{uuid.uuid4().hex[:12]}")
    monkeypatch.setattr(database, "json_dump",
                        lambda o: json.dumps(o, ensure_ascii=False))
    return s


@pytest.fixture()
def client(shim):
    """带种子数据的 FastAPI 测试客户端。"""
    from fastapi.testclient import TestClient
    from backend import ratelimit
    import backend.main as main

    ratelimit.reset()  # 避免限流记录跨用例互相影响
    with TestClient(main.app) as c:  # with 语句会触发 lifespan -> 建表 + 种子数据
        yield c


# ---------- 测试便捷函数 ----------

def login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def as_admin(client):
    return {"Authorization": "Bearer " + login(client, "admin", "admin123")["token"]}


def as_student(client, username="nurse001"):
    return {"Authorization": "Bearer " + login(client, username, "123456")["token"]}


def as_teacher(client, username="teacher001"):
    return {"Authorization": "Bearer " + login(client, username, "123456")["token"]}
