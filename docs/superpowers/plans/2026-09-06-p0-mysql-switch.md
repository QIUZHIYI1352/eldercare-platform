# P0 · SQLite → MySQL 切换 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 项目数据层从 SQLite 迁移到本机 MySQL（库 `eldercare`），现有 API 与前端行为不变。

**Architecture:** 只改 `backend/database.py` 一个数据访问层：连接改为 PyMySQL、建库建表脚本保持原表结构（做 MySQL 方言适配）、SQL 占位符在数据库层统一把 `?` 转 `%s`，路由层 SQL 基本不动。唯一例外是 `privacy.py` 的 `key` 保留字与 `ON CONFLICT` 方言。

**Tech Stack:** Python + PyMySQL（纯 Python 驱动）；MySQL 5.7+ / 8.x；无 ORM。

## Global Constraints

- MySQL 连接配置全部可被环境变量覆盖：`MYSQL_HOST`(localhost)、`MYSQL_PORT`(3306)、`MYSQL_USER`(root)、`MYSQL_PASSWORD`(空)、`MYSQL_DB`(eldercare)。
- 建库字符集 `utf8mb4` / `utf8mb4_unicode_ci`，启动时自动 `CREATE DATABASE IF NOT EXISTS`。
- 主键/唯一列的 `TEXT` 必须改为 `VARCHAR(64)`：MySQL 不允许 TEXT/BLOB 作键（无前缀长度会报错）。涉及：各表 `id`、`users.username`、`sessions.token`、`privacy_settings.key`。
- TEXT/BLOB 列不允许字面 DEFAULT（`TEXT DEFAULT '[]'` 会报错）：去掉所有 TEXT 列默认值，值由应用显式传入（现有 INSERT 均已显式传值）。
- 路由层与 CLI 的 SQL 写法保持 `?` 占位符不变；转换只发生在 `database.py`。
- `key` 是 MySQL 保留字，`privacy_settings` 相关 SQL 必须加反引号。
- 旧 SQLite 文件 `data/eldercare.db` 保留不动，不做数据迁移（开发环境 seed 重建）。
- 本沙箱对 `E:\eldercare-platform\.git` 有 ACL DENY，git 提交可能失败：每任务提交步骤若失败则跳过并在完成报告里说明，由用户在仓库手动提交。

## File Structure

- Modify `config.py`：新增 MySQL 配置，删除已无引用的 `DATA_DIR` / `DB_PATH`。
- Modify `requirements.txt`：新增 PyMySQL。
- Modify `backend/database.py`：整体替换为 MySQL 版。
- Modify `backend/routers/privacy.py`：保留字反引号 + `ON DUPLICATE KEY UPDATE`。
- Create `tests/test_database_compat.py`：纯逻辑自检（无需连接 MySQL）。

---

### Task 1: 依赖与配置

**Files:**
- Modify: `requirements.txt`
- Modify: `config.py`

**Interfaces:**
- Produces: `config.MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DB`（str/int，环境变量可覆盖），供 Task 2 使用。

- [ ] **Step 1: 安装依赖**

Run: `python -m pip install "PyMySQL>=1.1"`
Expected: 安装成功，无报错。

- [ ] **Step 2: 修改 requirements.txt**

在文件末尾追加一行：

```
PyMySQL>=1.1
```

- [ ] **Step 3: 修改 config.py**

把：

```python
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "eldercare.db")
```

替换为：

```python
# MySQL 连接配置（可用环境变量覆盖，密码等敏感信息不要写死在代码里）
MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DB = os.environ.get("MYSQL_DB", "eldercare")
```

- [ ] **Step 4: 验证配置可导入**

Run: `python -c "import config; print(config.MYSQL_DB, config.MYSQL_PORT)"`
Expected: `eldercare 3306`

- [ ] **Step 5: Commit**

```bash
git add requirements.txt config.py
git commit -m "feat(P0): 增加 MySQL 依赖与连接配置"
```
（ACL 拦截则跳过，见 Global Constraints）

### Task 2: database.py 切到 MySQL

**Files:**
- Modify: `backend/database.py`（整体替换）
- Create: `tests/test_database_compat.py`

**Interfaces:**
- Consumes: `config.MYSQL_*`（Task 1）
- Produces:
  - `convert_placeholders(sql: str) -> str`：`?` → `%s`
  - `split_script(sql: str) -> list[str]`：按 `;` 拆 DDL，忽略空段
  - `get_conn()`：PyMySQL 连接（DictCursor、utf8mb4、autocommit）
  - `init_db()`：先建库，再逐条执行建表 DDL
  - `query(sql, params=()) -> list[dict]`、`query_one(sql, params=()) -> dict|None`、`execute(sql, params=())`：行为与旧版一致
  - 保留：`now()`、`gen_id(prefix)`、`json_dump(obj)`、`json_load(s, default=None)`

- [ ] **Step 1: 写失败测试**

Create `tests/test_database_compat.py`：

```python
"""P0 数据库兼容层纯逻辑自检（无需连接 MySQL）"""
from backend.database import convert_placeholders, split_script


def test_convert_placeholders():
    sql = "SELECT * FROM users WHERE id = ? AND role = ?"
    assert convert_placeholders(sql) == \
        "SELECT * FROM users WHERE id = %s AND role = %s"


def test_split_script_ignores_empty():
    sql = "CREATE TABLE a (x TEXT);\n\n; CREATE TABLE b (y TEXT);"
    assert split_script(sql) == \
        ["CREATE TABLE a (x TEXT)", "CREATE TABLE b (y TEXT)"]


if __name__ == "__main__":
    test_convert_placeholders()
    test_split_script_ignores_empty()
    print("OK: database compat checks passed")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python tests/test_database_compat.py`
Expected: `ImportError: cannot import name 'convert_placeholders'`

- [ ] **Step 3: 替换 backend/database.py**

整文件替换为以下内容：

```python
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
"""


def init_db():
    """启动时调用：确保库与表存在（幂等）。"""
    _ensure_database()
    conn = get_conn()
    try:
        for stmt in split_script(_DDL):
            with conn.cursor() as cur:
                cur.execute(stmt)
    finally:
        conn.close()


def now() -> int:
    return int(time.time())


def gen_id(prefix="id") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def query(sql, params=()):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(convert_placeholders(sql), params or None)
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
            cur.execute(convert_placeholders(sql), params or None)
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
```

注意：该文件顶部 docstring 换成实际用途说明（如上）。不要保留旧的 `import sqlite3`、`_ensure_column`、`PRAGMA` 相关代码。

- [ ] **Step 4: 运行测试确认通过**

Run: `python tests/test_database_compat.py`
Expected: `OK: database compat checks passed`

- [ ] **Step 5: Commit**

```bash
git add backend/database.py tests/test_database_compat.py
git commit -m "feat(P0): 数据层切换 MySQL（占位符转换 + MySQL DDL）"
```
（ACL 拦截则跳过，见 Global Constraints）

### Task 3: privacy 路由 MySQL 方言修正

**Files:**
- Modify: `backend/routers/privacy.py`

**Interfaces:**
- Consumes: Task 2 的 `query` / `execute`（`?` 占位符自动转换）
- Produces: 与旧版完全一致的隐私设置读写 API

- [ ] **Step 1: 修改 _ensure_defaults 查询**

把：

```python
if not db.query_one("SELECT key FROM privacy_settings WHERE key=?", (k,)):
```

改为：

```python
if not db.query_one("SELECT `key` FROM privacy_settings WHERE `key`=?", (k,)):
```

- [ ] **Step 2: 修改 get_settings 查询**

把：

```python
rows = db.query("SELECT key, value, updated_at FROM privacy_settings")
```

改为：

```python
rows = db.query("SELECT `key`, value, updated_at FROM privacy_settings")
```

- [ ] **Step 3: 修改 update_settings 写入语句**

把：

```python
db.execute(
    "INSERT INTO privacy_settings (key, value, updated_at) VALUES (?,?,?) "
    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
    (k, "true" if v else "false", db.now()),
)
```

改为：

```python
db.execute(
    "INSERT INTO privacy_settings (`key`, value, updated_at) VALUES (?,?,?) "
    "ON DUPLICATE KEY UPDATE value=VALUES(value), updated_at=VALUES(updated_at)",
    (k, "true" if v else "false", db.now()),
)
```

- [ ] **Step 4: 静态检查**

Run: `python -c "import backend.routers.privacy"`
Expected: 无输出、无报错。

- [ ] **Step 5: Commit**

```bash
git add backend/routers/privacy.py
git commit -m "fix(P0): privacy 路由适配 MySQL 保留字与 upsert 语法"
```
（ACL 拦截则跳过，见 Global Constraints）

### Task 4: MySQL 冒烟验收

**Files:** 无代码改动（本任务只验证）

**Interfaces:**
- Consumes: Task 1~3 全部改动

**前置：** 需要用户提供本机 MySQL 连接信息（host/port/user/password）。拿到后把密码写入环境变量（不写入任何文件）。

- [ ] **Step 1: 设置环境变量**

Run（PowerShell，密码替换为实际值）：

```powershell
$env:MYSQL_HOST = "localhost"
$env:MYSQL_PORT = "3306"
$env:MYSQL_USER = "root"
$env:MYSQL_PASSWORD = "你的密码"
$env:MYSQL_DB = "eldercare"
```

- [ ] **Step 2: 建库建表 + 种子数据**

Run: `python -c "from backend import database as db; db.init_db(); from backend import seed; seed.seed(); print('init+seed OK')"`
Expected: `init+seed OK`，且控制台打印默认账号提示。

- [ ] **Step 3: 数据读写冒烟**

Run:

```powershell
python -c "from backend import database as db; print([r['username'] for r in db.query('SELECT username FROM users ORDER BY created_at LIMIT 5')])"
python -c "from backend import database as db; db.execute('UPDATE users SET organization=? WHERE username=?', ('冒烟测试', 'admin')); print(db.query_one('SELECT organization FROM users WHERE username=?', ('admin',)))"
```

Expected: 打印用户列表；第二次打印 `{'organization': '冒烟测试'}`。

- [ ] **Step 4: 服务启动与 API 冒烟**

Run: `python run.py`（另开终端）

```powershell
Invoke-RestMethod http://localhost:8000/api/health
$body = @{ username = "admin"; password = "admin123" } | ConvertTo-Json
$login = Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/auth/login -ContentType "application/json" -Body $body
$h = @{ Authorization = "Bearer $($login.token)" }
Invoke-RestMethod -Uri http://localhost:8000/api/processes -Headers $h
Invoke-RestMethod -Uri http://localhost:8000/api/privacy/settings -Headers $h
```

Expected: health 返回 ok；login 返回 token；processes 返回 items；privacy/settings 返回 6 个开关。

- [ ] **Step 5: 关闭服务并提交**

```bash
git add -A
git commit -m "chore(P0): MySQL 切换完成"
```
（ACL 拦截则跳过并报告，由用户手动提交）
