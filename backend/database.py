"""MySQL 数据访问层（PyMySQL 实现，无 ORM，保持轻量风格）

兼容约定：路由与 CLI 的 SQL 继续使用 sqlite 风格 ? 占位符，
由 convert_placeholders 统一转换为 MySQL 的 %s。
"""
import json
import time
import uuid

import pymysql

import config


def convert_placeholders(sql):
    """? -> %s（现有 SQL 中不存在字符串字面量 ?，可安全整体替换）"""
    return sql.replace("?", "%s")


def _prepare_sql(sql, params):
    """带参数时转义字面 %，避免 PyMySQL 的 % 格式化把 LIKE 通配符当占位符。"""
    if params:
        return sql.replace("%", "%%").replace("?", "%s")
    return sql.replace("?", "%s")


def split_script(sql):
    """按分号拆分多语句 DDL，忽略空段。"""
    return [s.strip() for s in sql.split(";") if s.strip()]


def _ensure_database():
    """连接服务器（不带库），不存在则创建 eldercare 库。"""
    conn = pymysql.connect(
        host=config.MYSQL_HOST, port=config.MYSQL_PORT,
        user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
        charset="utf8mb4", autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE DATABASE IF NOT EXISTS `%s` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                % config.MYSQL_DB
            )
    finally:
        conn.close()


def get_conn():
    return pymysql.connect(
        host=config.MYSQL_HOST, port=config.MYSQL_PORT,
        user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DB, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=True,
    )


_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(64) PRIMARY KEY,
    username VARCHAR(64) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    organization TEXT,
    phone TEXT,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS sessions (
    token VARCHAR(64) PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS processes (
    id VARCHAR(64) PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    description TEXT,
    steps TEXT,
    created_by TEXT,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS actions (
    id VARCHAR(64) PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    description TEXT,
    conditions TEXT,
    duration REAL DEFAULT 1.0,
    sample_ref TEXT,
    template_type TEXT,
    template_data TEXT,
    created_by TEXT,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS devices (
    id VARCHAR(64) PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT,
    url TEXT,
    location TEXT,
    status TEXT,
    privacy_mask INTEGER DEFAULT 1,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS monitoring_records (
    id VARCHAR(64) PRIMARY KEY,
    device_id TEXT,
    process_id TEXT,
    user_id TEXT,
    started_at INTEGER,
    ended_at INTEGER,
    detected_actions TEXT,
    missed_steps TEXT,
    completed_steps TEXT,
    status TEXT
);

CREATE TABLE IF NOT EXISTS training_records (
    id VARCHAR(64) PRIMARY KEY,
    user_id TEXT,
    process_id TEXT,
    task_id TEXT,
    capture_record_id TEXT,
    score REAL DEFAULT 0,
    completed_steps TEXT,
    missed_steps TEXT,
    duration INTEGER DEFAULT 0,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS privacy_settings (
    `key` VARCHAR(64) PRIMARY KEY,
    value TEXT,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id VARCHAR(64) PRIMARY KEY,
    user_id TEXT,
    username TEXT,
    action TEXT,
    detail TEXT,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS motion_capture_records (
    id VARCHAR(64) PRIMARY KEY,
    user_id TEXT,
    task_id TEXT,
    process_id TEXT,
    source TEXT,
    source_type TEXT,
    status TEXT,
    started_at INTEGER,
    ended_at INTEGER,
    steps TEXT,
    segments TEXT,
    frames TEXT,
    score REAL,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS classes (
    id VARCHAR(64) PRIMARY KEY,
    name TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    description TEXT,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS messages (
    id VARCHAR(64) PRIMARY KEY,
    from_user_id TEXT NOT NULL,
    to_user_id TEXT NOT NULL,
    body TEXT,
    kind TEXT,
    related_record_id TEXT,
    read_at INTEGER,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS class_members (
    class_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    joined_at INTEGER,
    PRIMARY KEY (class_id, user_id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id VARCHAR(64) PRIMARY KEY,
    class_id TEXT NOT NULL,
    process_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    deadline INTEGER,
    status TEXT,
    created_by TEXT,
    created_at INTEGER
);
"""


def init_db():
    """启动时调用：确保库与表存在（幂等）。"""
    _ensure_database()
    conn = get_conn()
    try:
        for stmt in split_script(_DDL):
            with conn.cursor() as cur:
                cur.execute(stmt)
        _ensure_columns(conn, "training_records",
                        ["task_id", "capture_record_id"], "TEXT")
    finally:
        conn.close()


def _ensure_columns(conn, table, columns, ddl):
    """幂等补列：旧库升级用（MySQL information_schema 检查）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (table,),
        )
        existing = {r["COLUMN_NAME"] for r in cur.fetchall()}
    for col in columns:
        if col not in existing:
            with conn.cursor() as cur:
                cur.execute(f"ALTER TABLE `{table}` ADD COLUMN {col} {ddl}")


def now() -> int:
    return int(time.time())


def gen_id(prefix="id") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def query(sql, params=()):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_prepare_sql(sql, params), params or None)
            return list(cur.fetchall())
    finally:
        conn.close()


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_prepare_sql(sql, params), params or None)
            conn.commit()
    finally:
        conn.close()


def json_dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def json_load(s, default=None):
    if default is None:
        default = []
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default
