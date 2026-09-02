"""SQLite 数据访问层（标准库实现，无额外 ORM 依赖）"""
import json
import os
import sqlite3
import time
import uuid

import config


def get_conn():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # 多进程部署（web + monitor 容器共享数据库）时提升并发安全
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            organization TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            created_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS processes (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT DEFAULT '基础护理',
            description TEXT DEFAULT '',
            steps TEXT DEFAULT '[]',       -- JSON: [{order,name,action_id,detail,duration}]
            created_by TEXT DEFAULT '',
            created_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS actions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT DEFAULT '通用',
            description TEXT DEFAULT '',
            conditions TEXT DEFAULT '[]',  -- JSON: [{joint,op,value}] 关节角度判定规则
            duration REAL DEFAULT 1.0,     -- 需持续秒数
            sample_ref TEXT DEFAULT '',    -- 示例视频/图片引用
            template_type TEXT DEFAULT 'rule',   -- rule=角度规则 | sequence=骨骼序列模板(DTW)
            template_data TEXT DEFAULT '',        -- JSON: {vectors, threshold, frames}
            created_by TEXT DEFAULT '',
            created_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS devices (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT DEFAULT 'USB摄像头',
            url TEXT DEFAULT '0',          -- 0 表示本机默认摄像头，或 rtsp/http 地址
            location TEXT DEFAULT '',
            status TEXT DEFAULT 'offline',
            privacy_mask INTEGER DEFAULT 1,
            created_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS monitoring_records (
            id TEXT PRIMARY KEY,
            device_id TEXT DEFAULT '',
            process_id TEXT DEFAULT '',
            user_id TEXT DEFAULT '',
            started_at INTEGER,
            ended_at INTEGER,
            detected_actions TEXT DEFAULT '[]',
            missed_steps TEXT DEFAULT '[]',
            completed_steps TEXT DEFAULT '[]',
            status TEXT DEFAULT 'running'
        );

        CREATE TABLE IF NOT EXISTS training_records (
            id TEXT PRIMARY KEY,
            user_id TEXT DEFAULT '',
            process_id TEXT DEFAULT '',
            score REAL DEFAULT 0,
            completed_steps TEXT DEFAULT '[]',
            missed_steps TEXT DEFAULT '[]',
            duration INTEGER DEFAULT 0,
            created_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS privacy_settings (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT '',
            updated_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id TEXT PRIMARY KEY,
            user_id TEXT DEFAULT '',
            username TEXT DEFAULT '',
            action TEXT DEFAULT '',
            detail TEXT DEFAULT '',
            created_at INTEGER
        );
        """
    )
    conn.commit()
    # 迁移：为旧库补充新列
    _ensure_column(conn, "actions", "template_type", "TEXT DEFAULT 'rule'")
    _ensure_column(conn, "actions", "template_data", "TEXT DEFAULT ''")
    conn.close()


def _ensure_column(conn, table, column, ddl):
    """SQLite 轻量迁移：列不存在时 ALTER TABLE 添加。"""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        conn.commit()


def now() -> int:
    return int(time.time())


def gen_id(prefix="id") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def query(sql, params=()):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
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
