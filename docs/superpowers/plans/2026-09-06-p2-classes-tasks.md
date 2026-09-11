# P2 · 班级与任务 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 教师创建班级、把学员按账号加入班级、发布任务（班级+流程+截止时间）；学员看到自己班级的已发布任务与完成状态。

**Architecture:** 新增 `classes`、`class_members`、`tasks` 三张 MySQL 表；新增 `backend/routers/classes.py`（班级与成员）与 `backend/routers/tasks.py`（教师任务管理 + 学员我的任务）；前端新增教师「班级管理」「任务发布」页与学员「我的任务」页。任务不落盘每学员状态，由该学员是否存在 finished 的 `training_records`（task_id 关联）推导。

**Tech Stack:** Python + FastAPI + PyMySQL + 原生 JS SPA（沿用既有结构）。

## Global Constraints

- 教师角色 = `admin` / `elderly_service_teacher`；学员角色 = `nursing_student` / `long_term_caregiver` / `elderly_caregiver`。注册接口已禁止学员注册为教师，此处服务端继续校验。
- 数据归属：普通教师只能操作自己创建的班级/任务；admin 可见与操作全部。
- 入班成员只能是学员角色账号；重复入班静默跳过，未知账号/教师账号返回错误清单。
- 任务状态：`published` / `closed`；新建即发布。学员侧任务状态 = `done`（存在 finished 记录）> `overdue`（未完成且过截止时间）> `pending`。
- 新增表 MySQL 方言：主键/唯一列 VARCHAR(64)，TEXT 无字面默认。
- 本沙箱对 `.git` 无写权限：commit 由用户在自己的终端执行。

## File Structure

- Modify `backend/database.py`：`_DDL` 增加三张表。
- Create `backend/routers/classes.py`：班级 CRUD + 成员管理。
- Create `backend/routers/tasks.py`：教师任务管理 + 学员我的任务。
- Modify `backend/main.py`：注册两个路由。
- Create `tests/test_classroom.py`：权限/状态纯逻辑自检。
- Modify `frontend/js/app.js`：教师「班级管理」「任务发布」、学员「我的任务」。

---

### Task 1: 数据表 + 纯逻辑自检

**Files:**
- Modify: `backend/database.py`
- Create: `backend/routers/tasks.py`（先放纯函数，路由后续 Task 3 补全）
- Create: `tests/test_classroom.py`

**Interfaces:**
- Produces:
  - `can_manage(user, owner_id) -> bool`：admin 可管全部；普通教师只管自己；学员 False。
  - `task_state_for(deadline, done, now_ts) -> "done"|"overdue"|"pending"`。

- [ ] **Step 1: database.py 增加三张表**

在 `_DDL` 的 `motion_capture_records` 后追加：

```sql

CREATE TABLE IF NOT EXISTS classes (
    id VARCHAR(64) PRIMARY KEY,
    name TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    description TEXT,
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
```

- [ ] **Step 2: 先建 tasks.py 的纯函数（路由同文件，本步只写顶部）**

Create `backend/routers/tasks.py` 首部（后续 Task 3 在末尾追加路由）：

```python
"""任务接口：教师发布/管理任务，学员查看我的任务"""
from fastapi import APIRouter, Depends, HTTPException

from backend import database as db
from .auth_router import current_user
from config import ROLES

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

TEACHER_ROLES = {"admin", "elderly_service_teacher"}
STUDENT_ROLES = {"nursing_student", "long_term_caregiver", "elderly_caregiver"}


def can_manage(user, owner_id):
    """admin 可管理全部；普通教师只能管理自己创建的；其余角色无权限。"""
    if user.get("role") == "admin":
        return True
    return user.get("role") in TEACHER_ROLES and user.get("id") == owner_id


def task_state_for(deadline, done, now_ts):
    """已完成 > 已逾期 > 待完成。"""
    if done:
        return "done"
    if deadline and int(deadline) < now_ts:
        return "overdue"
    return "pending"
```

- [ ] **Step 3: 写失败测试**

Create `tests/test_classroom.py`：

```python
"""P2 班级/任务纯逻辑自检（无需 MySQL）"""
from backend.routers.tasks import can_manage, task_state_for


def test_can_manage():
    admin = {"role": "admin", "id": "u1"}
    teacher = {"role": "elderly_service_teacher", "id": "u2"}
    other_teacher = {"role": "elderly_service_teacher", "id": "u3"}
    student = {"role": "nursing_student", "id": "u4"}
    assert can_manage(admin, "anyone") is True
    assert can_manage(teacher, "u2") is True
    assert can_manage(teacher, "u3") is False
    assert can_manage(student, "u2") is False


def test_task_state():
    now = 1_700_000_000
    assert task_state_for(now + 100, False, now) == "pending"
    assert task_state_for(now - 100, False, now) == "overdue"
    assert task_state_for(now - 100, True, now) == "done"


if __name__ == "__main__":
    test_can_manage()
    test_task_state()
    print("OK: classroom logic checks passed")
```

- [ ] **Step 4: 运行测试确认失败/通过**

先运行确认失败：`python -m tests.test_classroom` → `ModuleNotFoundError`（tasks.py 尚未创建）。
再创建 tasks.py 首部（Step 2）后运行：`python -m tests.test_classroom` → `OK: classroom logic checks passed`。

- [ ] **Step 5: Commit**

```bash
git add backend/database.py backend/routers/tasks.py tests/test_classroom.py
git commit -m "feat(P2): 班级/任务表与纯逻辑"
```
（用户执行）

### Task 2: 班级与成员接口

**Files:**
- Create: `backend/routers/classes.py`
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `can_manage`（Task 1）、P0 数据层
- Produces:
  - `GET /api/classes` → `{items:[{id,name,member_count,created_at}]}`
  - `POST /api/classes` `{name, description?}` → `{id}`
  - `PUT /api/classes/{cid}` `{name, description?}`
  - `DELETE /api/classes/{cid}`
  - `GET /api/classes/{cid}/members` → `{items:[学员用户]}`
  - `POST /api/classes/{cid}/members` `{usernames:[...]}` → `{added, errors}`
  - `DELETE /api/classes/{cid}/members/{uid}`

- [ ] **Step 1: 创建 classes.py**

```python
"""班级管理：班级 CRUD + 学员按账号加入/移除"""
from fastapi import APIRouter, Depends, HTTPException

from backend import auth
from backend import database as db
from .auth_router import current_user
from .tasks import TEACHER_ROLES, STUDENT_ROLES, can_manage

router = APIRouter(prefix="/api/classes", tags=["classes"])


def _own_scope(user):
    return "" if user["role"] == "admin" else "WHERE owner_user_id = ?"


@router.get("")
def list_classes(user=Depends(current_user)):
    if user["role"] not in TEACHER_ROLES:
        raise HTTPException(status_code=403, detail="无权限")
    where = _own_scope(user)
    rows = db.query(
        "SELECT c.id, c.name, c.description, c.created_at, "
        "(SELECT COUNT(*) FROM class_members m WHERE m.class_id = c.id) AS member_count "
        "FROM classes c " + where + " ORDER BY c.created_at DESC",
        (user["id"],) if where else (),
    )
    return {"items": rows}


@router.post("")
def create_class(payload: dict, user=Depends(current_user)):
    if user["role"] not in TEACHER_ROLES:
        raise HTTPException(status_code=403, detail="无权限")
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="班级名称必填")
    cid = db.gen_id("cls")
    db.execute("INSERT INTO classes (id, name, owner_user_id, description, created_at) VALUES (?,?,?,?,?)",
               (cid, name, user["id"], payload.get("description") or "", db.now()))
    auth.audit(user, "create_class", f"创建班级 {name}")
    return {"id": cid}


def _get_class(cid, user):
    row = db.query_one("SELECT * FROM classes WHERE id = ?", (cid,))
    if not row:
        raise HTTPException(status_code=404, detail="班级不存在")
    if not can_manage(user, row["owner_user_id"]):
        raise HTTPException(status_code=403, detail="无权限")
    return row


@router.put("/{cid}")
def update_class(cid: str, payload: dict, user=Depends(current_user)):
    row = _get_class(cid, user)
    db.execute("UPDATE classes SET name=?, description=? WHERE id=?",
               ((payload.get("name") or row["name"]).strip(),
                payload.get("description") or row["description"], cid))
    auth.audit(user, "update_class", f"更新班级 {row['name']}")
    return {"ok": True}


@router.delete("/{cid}")
def delete_class(cid: str, user=Depends(current_user)):
    row = _get_class(cid, user)
    db.execute("DELETE FROM classes WHERE id = ?", (cid,))
    db.execute("DELETE FROM class_members WHERE class_id = ?", (cid,))
    auth.audit(user, "delete_class", f"删除班级 {row['name']}")
    return {"ok": True}


@router.get("/{cid}/members")
def list_members(cid: str, user=Depends(current_user)):
    _get_class(cid, user)
    rows = db.query(
        "SELECT u.id, u.username, u.name, u.role, u.organization FROM class_members m "
        "JOIN users u ON m.user_id = u.id WHERE m.class_id = ? ORDER BY u.created_at",
        (cid,),
    )
    return {"items": rows}


@router.post("/{cid}/members")
def add_members(cid: str, payload: dict, user=Depends(current_user)):
    _get_class(cid, user)
    usernames = [(u or "").strip() for u in payload.get("usernames") or []]
    usernames = [u for u in usernames if u]
    added, errors = [], []
    for uname in usernames:
        row = db.query_one("SELECT id, role FROM users WHERE username = ?", (uname,))
        if not row:
            errors.append(f"账号不存在: {uname}")
            continue
        if row["role"] not in STUDENT_ROLES:
            errors.append(f"非学员账号: {uname}")
            continue
        exists = db.query_one("SELECT 1 FROM class_members WHERE class_id=? AND user_id=?",
                              (cid, row["id"]))
        if exists:
            continue
        db.execute("INSERT INTO class_members (class_id, user_id, joined_at) VALUES (?,?,?)",
                   (cid, row["id"], db.now()))
        added.append(uname)
    auth.audit(user, "add_class_members", f"班级 {cid} 加入学员 {len(added)} 人")
    return {"added": added, "errors": errors}


@router.delete("/{cid}/members/{uid}")
def remove_member(cid: str, uid: str, user=Depends(current_user)):
    _get_class(cid, user)
    db.execute("DELETE FROM class_members WHERE class_id=? AND user_id=?", (cid, uid))
    return {"ok": True}
```

- [ ] **Step 2: main.py 注册**

import 列表加 `classes`，并在 `app.include_router(assessment.router)` 后加：

```python
app.include_router(classes.router)
```

- [ ] **Step 3: 语法检查**

Run: `python -c "import backend.routers.classes"`
Expected: 无报错。

- [ ] **Step 4: Commit**

```bash
git add backend/routers/classes.py backend/main.py
git commit -m "feat(P2): 班级与成员管理接口"
```
（用户执行）

### Task 3: 任务接口

**Files:**
- Modify: `backend/routers/tasks.py`（在 Task 1 首部后追加路由）

**Interfaces:**
- Consumes: `can_manage` / `task_state_for`（Task 1）、`_get_class` 的归属逻辑（在 classes.py，本任务内重复实现同规则以保证文件独立）
- Produces:
  - `GET /api/tasks`（教师）→ 任务列表（含班级/流程名）
  - `POST /api/tasks` `{class_id, process_id, title, description?, deadline}` → `{id}`
  - `PUT /api/tasks/{tid}` → 更新字段/关闭
  - `DELETE /api/tasks/{tid}`
  - `GET /api/tasks/mine`（学员）→ 我的任务（含状态与最新得分）

- [ ] **Step 1: 追加教师端任务接口**

```python
def _get_task(tid, user):
    row = db.query_one("SELECT * FROM tasks WHERE id = ?", (tid,))
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    cls = db.query_one("SELECT * FROM classes WHERE id = ?", (row["class_id"],))
    if not cls or not can_manage(user, cls["owner_user_id"]):
        raise HTTPException(status_code=403, detail="无权限")
    return row


@router.get("")
def list_tasks(user=Depends(current_user)):
    if user["role"] not in TEACHER_ROLES:
        raise HTTPException(status_code=403, detail="无权限")
    if user["role"] == "admin":
        where = ""
    else:
        where = "WHERE c.owner_user_id = ?"
    rows = db.query(
        "SELECT t.id, t.class_id, t.process_id, t.title, t.description, "
        "t.deadline, t.status, t.created_at, c.name AS class_name, "
        "p.name AS process_name FROM tasks t "
        "JOIN classes c ON t.class_id = c.id "
        "JOIN processes p ON t.process_id = p.id " + where +
        " ORDER BY t.created_at DESC",
        (user["id"],) if where else (),
    )
    return {"items": rows}


@router.post("")
def create_task(payload: dict, user=Depends(current_user)):
    if user["role"] not in TEACHER_ROLES:
        raise HTTPException(status_code=403, detail="无权限")
    class_id = payload.get("class_id") or ""
    process_id = payload.get("process_id") or ""
    cls = db.query_one("SELECT * FROM classes WHERE id = ?", (class_id,))
    if not cls or not can_manage(user, cls["owner_user_id"]):
        raise HTTPException(status_code=403, detail="无权限")
    proc = db.query_one("SELECT id FROM processes WHERE id = ?", (process_id,))
    if not proc:
        raise HTTPException(status_code=400, detail="流程不存在")
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="任务标题必填")
    deadline = int(payload.get("deadline") or 0)
    if not deadline:
        raise HTTPException(status_code=400, detail="截止时间必填")
    tid = db.gen_id("task")
    db.execute(
        "INSERT INTO tasks (id, class_id, process_id, title, description, "
        "deadline, status, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (tid, class_id, process_id, title, payload.get("description") or "",
         deadline, "published", user["id"], db.now()),
    )
    auth.audit(user, "create_task", f"发布任务 {title}")
    return {"id": tid}


@router.put("/{tid}")
def update_task(tid: str, payload: dict, user=Depends(current_user)):
    row = _get_task(tid, user)
    db.execute(
        "UPDATE tasks SET title=?, description=?, deadline=?, status=? WHERE id=?",
        ((payload.get("title") or row["title"]).strip(),
         payload.get("description", row["description"]),
         int(payload.get("deadline") or row["deadline"]),
         payload.get("status") or row["status"], tid),
    )
    auth.audit(user, "update_task", f"更新任务 {row['title']}")
    return {"ok": True}


@router.delete("/{tid}")
def delete_task(tid: str, user=Depends(current_user)):
    row = _get_task(tid, user)
    db.execute("DELETE FROM tasks WHERE id = ?", (tid,))
    auth.audit(user, "delete_task", f"删除任务 {row['title']}")
    return {"ok": True}
```

- [ ] **Step 2: 追加学员端「我的任务」**

注意：`/mine` 必须定义在 `/` 之后、`/{tid}` 之类的动态路由之前（FastAPI 按声明顺序匹配，动态 `PUT/{tid}`/`DELETE/{tid}` 与 GET 方法不冲突，但为了清晰仍先声明）。

```python
@router.get("/mine")
def my_tasks(user=Depends(current_user)):
    if user["role"] not in STUDENT_ROLES:
        raise HTTPException(status_code=403, detail="无权限")
    now_ts = db.now()
    rows = db.query(
        "SELECT t.id, t.class_id, t.process_id, t.title, t.description, "
        "t.deadline, t.status, t.created_at, c.name AS class_name, "
        "p.name AS process_name, p.steps, "
        "(SELECT score FROM training_records tr WHERE tr.task_id = t.id "
        " AND tr.user_id = ? ORDER BY tr.created_at DESC LIMIT 1) AS latest_score "
        "FROM tasks t "
        "JOIN classes c ON t.class_id = c.id "
        "JOIN processes p ON t.process_id = p.id "
        "WHERE t.status = 'published' AND t.class_id IN "
        "(SELECT m.class_id FROM class_members m WHERE m.user_id = ?) "
        "ORDER BY t.deadline ASC",
        (user["id"], user["id"]),
    )
    for r in rows:
        r["steps_count"] = len(db.json_load(r.pop("steps")))
        r["state"] = task_state_for(
            r["deadline"], r["latest_score"] is not None, now_ts)
    return {"items": rows}
```

- [ ] **Step 3: 语法与自检**

Run: `python -m tests.test_classroom` 与 `python -c "import backend.routers.tasks"`
Expected: `OK: classroom logic checks passed`，import 无报错。

- [ ] **Step 4: Commit**

```bash
git add backend/routers/tasks.py
git commit -m "feat(P2): 任务发布与我的任务接口"
```
（用户执行）

### Task 4: 前端「班级管理」「任务发布」「我的任务」

**Files:**
- Modify: `frontend/js/app.js`

**Interfaces:**
- Consumes: Task 2/3 全部接口
- Produces: 导航 keys `classes`（教师）、`tasks`（教师发布）、`mytasks`（学员）

- [ ] **Step 1: 导航按角色分流**

把 `navItems()` 里的 `const isAdmin = ...` 改为：

```js
    const role = this.state.user && this.state.user.role;
    const isAdmin = role === "admin";
    const isTeacher = isAdmin || role === "elderly_service_teacher";
    const isStudent = role === "nursing_student" || role === "long_term_caregiver" || role === "elderly_caregiver";
```

base 数组在 `{ key: "training"...}` 后加：

```js
    if (isTeacher) {
      base.push({ key: "classes", ico: "🏫", label: "班级管理" });
      base.push({ key: "tasks", ico: "📨", label: "任务发布" });
    }
    if (isStudent) {
      base.push({ key: "mytasks", ico: "📋", label: "我的任务" });
    }
```

`titles` 加：

```js
      classes: "班级管理", tasks: "任务发布", mytasks: "我的任务",
```

`views` 加：

```js
      classes: () => this.renderClasses(), tasks: () => this.renderTasks(),
      mytasks: () => this.renderMyTasks(),
```

- [ ] **Step 2: 班级管理页**

```js
  // ---------- 班级管理 ----------
  async renderClasses() {
    const c = document.getElementById("content");
    const data = await this.api("/api/classes");
    c.innerHTML = `<div class="card"><div class="card-head"><h3>我的班级</h3>
      <button class="btn btn-primary" onclick="App.editClass()">+ 新建班级</button></div>
      <div id="class-list"></div></div>`;
    const list = document.getElementById("class-list");
    if (!data.items.length) { list.innerHTML = this.emptyHtml("暂无班级"); return; }
    list.innerHTML = data.items.map(cls => `
      <div class="card" style="box-shadow:none;border:1px solid var(--border)">
        <div class="card-head"><h3>${this.esc(cls.name)}
          <span class="tag tag-blue">${cls.member_count} 人</span></h3>
          <div>
            <button class="btn btn-sm" onclick="App.renderClassMembers('${cls.id}')">成员管理</button>
            <button class="btn btn-sm btn-primary" onclick="App.editClass('${cls.id}')">编辑</button>
            <button class="btn btn-sm btn-danger" onclick="App.delClass('${cls.id}')">删除</button>
          </div></div>
        <p style="color:var(--muted);font-size:13px">${this.esc(cls.description || "")}</p>
      </div>`).join("");
  },
  async editClass(cid) {
    let cls = { name: "", description: "" };
    if (cid) {
      const data = await this.api("/api/classes");
      cls = data.items.find(x => x.id === cid);
    }
    this.openModal(`
      <h3>${cid ? "编辑" : "新建"}班级</h3>
      <div class="form-row"><label>班级名称</label><input id="c-name" value="${this.esc(cls.name)}"></div>
      <div class="form-row"><label>描述（可选）</label><textarea id="c-desc">${this.esc(cls.description || "")}</textarea></div>
      <div class="modal-actions">
        <button class="btn" onclick="App.closeModal()">取消</button>
        <button class="btn btn-primary" onclick="App.saveClass('${cid || ""}')">保存</button>
      </div>`);
  },
  async saveClass(cid) {
    const payload = { name: document.getElementById("c-name").value.trim(),
      description: document.getElementById("c-desc").value.trim() };
    if (!payload.name) { alert("请填写班级名称"); return; }
    try {
      if (cid) await this.api("/api/classes/" + cid, "PUT", payload);
      else await this.api("/api/classes", "POST", payload);
      this.closeModal(); this.go("classes");
    } catch (e) { alert(e.message); }
  },
  async delClass(cid) {
    if (!confirm("确认删除该班级？成员关系会一并删除")) return;
    await this.api("/api/classes/" + cid, "DELETE");
    this.go("classes");
  },
  async renderClassMembers(cid) {
    const data = await this.api("/api/classes/" + cid + "/members");
    this.openModal(`<h3>成员管理</h3>
      <div class="form-row"><label>按账号添加学员（逗号分隔多个）</label>
        <input id="m-names" placeholder="nurse001, elderly001"></div>
      <button class="btn btn-primary" onclick="App.addClassMembers('${cid}')">添加</button>
      <p id="m-msg" class="msg"></p>
      <table style="margin-top:12px"><thead><tr><th>账号</th><th>姓名</th><th>角色</th><th>操作</th></tr></thead>
      <tbody>${data.items.map(m => `<tr><td>${this.esc(m.username)}</td><td>${this.esc(m.name)}</td>
        <td>${this.esc(this.roleNames[m.role] || m.role)}</td>
        <td><button class="btn btn-sm btn-danger" onclick="App.removeClassMember('${cid}','${m.id}')">移除</button></td></tr>`).join("")
        || `<tr><td colspan="4">${this.emptyHtml("暂无成员")}</td></tr>`}</tbody></table>
      <div class="modal-actions"><button class="btn" onclick="App.closeModal()">关闭</button></div>`);
  },
  async addClassMembers(cid) {
    const msg = document.getElementById("m-msg");
    const usernames = document.getElementById("m-names").value.split(/[,，\s]+/).filter(Boolean);
    try {
      const r = await this.api("/api/classes/" + cid + "/members", "POST", { usernames });
      msg.textContent = `已添加 ${r.added.length} 人` + (r.errors.length ? "；失败: " + r.errors.join("；") : "");
      this.renderClassMembers(cid);
    } catch (e) { msg.textContent = e.message; }
  },
  async removeClassMember(cid, uid) {
    if (!confirm("确认移除该学员？")) return;
    await this.api("/api/classes/" + cid + "/members/" + uid, "DELETE");
    this.renderClassMembers(cid);
  },
```

- [ ] **Step 3: 任务发布页（教师）**

```js
  // ---------- 任务发布 ----------
  async renderTasks() {
    const c = document.getElementById("content");
    const [data, clsData] = await Promise.all([
      this.api("/api/tasks"), this.api("/api/classes")]);
    c.innerHTML = `<div class="card"><div class="card-head"><h3>已发布任务</h3>
      <button class="btn btn-primary" onclick="App.editTask()">+ 发布任务</button></div>
      <table><thead><tr><th>标题</th><th>班级</th><th>流程</th><th>截止</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>${data.items.map(t => `<tr>
        <td><b>${this.esc(t.title)}</b></td><td>${this.esc(t.class_name)}</td>
        <td>${this.esc(t.process_name)}</td>
        <td>${new Date((t.deadline || 0) * 1000).toLocaleString()}</td>
        <td><span class="tag ${t.status === "published" ? "tag-green" : "tag-gray"}">${t.status === "published" ? "进行中" : "已关闭"}</span></td>
        <td><button class="btn btn-sm btn-primary" onclick="App.editTask('${t.id}')">编辑</button>
            <button class="btn btn-sm btn-danger" onclick="App.delTask('${t.id}')">删除</button></td>
      </tr>`).join("") || `<tr><td colspan="6">${this.emptyHtml("暂无任务")}</td></tr>`}</tbody></table></div>
      <div class="card"><div class="card-head"><h3>提示</h3></div>
      <p style="color:var(--muted)">发布前需先建班级（任务发给整个班级）；学员完成状态在 P3 接入后自动显示。</p></div>`;
    window._teacherClasses = clsData.items;
  },
  async editTask(tid) {
    let t = { title: "", description: "", status: "published" };
    if (tid) {
      const data = await this.api("/api/tasks");
      t = data.items.find(x => x.id === tid);
    }
    const procs = await this.api("/api/processes");
    const clsOpts = (window._teacherClasses || []).map(x => `<option value="${x.id}">${this.esc(x.name)}</option>`).join("");
    const procOpts = procs.items.map(p => `<option value="${p.id}">${this.esc(p.name)}</option>`).join("");
    this.openModal(`
      <h3>${tid ? "编辑" : "发布"}任务</h3>
      <div class="form-row"><label>标题</label><input id="t-title" value="${this.esc(t.title)}"></div>
      <div class="form-row"><label>班级</label><select id="t-class">${clsOpts.replace(`value="${t.class_id}"`, `value="${t.class_id}" selected`)}</select></div>
      <div class="form-row"><label>流程</label><select id="t-proc">${procOpts.replace(`value="${t.process_id}"`, `value="${t.process_id}" selected`)}</select></div>
      <div class="form-row"><label>截止时间</label>
        <input id="t-deadline" type="datetime-local" value="${t.deadline ? new Date(t.deadline * 1000).toISOString().slice(0, 16) : ""}"></div>
      <div class="form-row"><label>描述（可选）</label><textarea id="t-desc">${this.esc(t.description || "")}</textarea></div>
      <div class="modal-actions">
        <button class="btn" onclick="App.closeModal()">取消</button>
        <button class="btn btn-primary" onclick="App.saveTask('${tid || ""}')">保存</button>
      </div>`);
  },
  async saveTask(tid) {
    const deadline = new Date(document.getElementById("t-deadline").value).getTime();
    const payload = {
      title: document.getElementById("t-title").value.trim(),
      class_id: document.getElementById("t-class").value,
      process_id: document.getElementById("t-proc").value,
      deadline: Math.floor(deadline / 1000),
      description: document.getElementById("t-desc").value.trim(),
    };
    if (!payload.title || !deadline) { alert("标题与截止时间必填"); return; }
    try {
      if (tid) await this.api("/api/tasks/" + tid, "PUT", payload);
      else await this.api("/api/tasks", "POST", payload);
      this.closeModal(); this.go("tasks");
    } catch (e) { alert(e.message); }
  },
  async delTask(tid) {
    if (!confirm("确认删除该任务？")) return;
    await this.api("/api/tasks/" + tid, "DELETE");
    this.go("tasks");
  },
```

- [ ] **Step 4: 我的任务页（学员）**

```js
  // ---------- 我的任务 ----------
  async renderMyTasks() {
    const c = document.getElementById("content");
    const data = await this.api("/api/tasks/mine");
    const stateMap = { pending: ["tag-gray", "待完成"], overdue: ["tag-red", "已逾期"], done: ["tag-green", "已完成"] };
    c.innerHTML = `<div class="card"><div class="card-head"><h3>我的任务</h3></div>
      <div id="mytask-list"></div></div>`;
    const list = document.getElementById("mytask-list");
    if (!data.items.length) { list.innerHTML = this.emptyHtml("暂无任务，等待老师发布"); return; }
    list.innerHTML = data.items.map(t => {
      const [tagCls, tagTxt] = stateMap[t.state] || stateMap.pending;
      return `<div class="card" style="box-shadow:none;border:1px solid var(--border)">
        <div class="card-head"><h3>${this.esc(t.title)} <span class="tag ${tagCls}">${tagTxt}</span></h3>
          <span style="color:var(--muted);font-size:12px">${t.latest_score == null ? "" : "最新得分 " + t.latest_score + " 分"}</span></div>
        <p style="color:var(--muted);font-size:13px">班级：${this.esc(t.class_name)} ｜ 流程：${this.esc(t.process_name)}（${t.steps_count} 步）</p>
        <p style="color:var(--muted);font-size:13px">截止：${new Date((t.deadline || 0) * 1000).toLocaleString()}</p>
      </div>`;
    }).join("");
  },
```

- [ ] **Step 5: 语法检查**

Run: `node --check frontend/js/app.js`
Expected: 无输出且退出码 0。

- [ ] **Step 6: Commit**

```bash
git add frontend/js/app.js
git commit -m "feat(P2): 班级管理/任务发布/我的任务页面"
```
（用户执行）

### Task 5: 集成验收

**Files:** 无

- [ ] **Step 1: 自检**

Run: `python -m tests.test_classroom`
Expected: `OK: classroom logic checks passed`

- [ ] **Step 2: API 冒烟**

用 admin 登录后按序执行（PowerShell）：

```powershell
# 建班级
$cls = Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/classes -Headers $hd -ContentType "application/json" -Body (@{ name = "冒烟班级"; description = "P2 冒烟" } | ConvertTo-Json)
# 加学员
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/classes/$($cls.id)/members" -Headers $hd -ContentType "application/json" -Body (@{ usernames = @("nurse001", "不存在账号") } | ConvertTo-Json)
# 发任务（deadline = 当前时间 + 7 天）
$deadline = [int][double]::Parse((Get-Date -UFormat %s)) + 604800
$task = Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/tasks -Headers $hd -ContentType "application/json" -Body (@{ class_id = $cls.id; process_id = (Invoke-RestMethod http://localhost:8000/api/processes -Headers $hd).items[0].id; title = "冒烟任务"; deadline = $deadline } | ConvertTo-Json)
```

Expected：班级返回 id；members 返回 added=["nurse001"]、errors=["账号不存在: 不存在账号"]；任务返回 id。
再用 `nurse001 / 123456` 登录调 `GET /api/tasks/mine`，Expected：能看到「冒烟任务」且 state=pending。

- [ ] **Step 3: 清理冒烟数据（管理员）**

删除冒烟任务、班级后，`GET /api/tasks/mine` 恢复为空。

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(P2): 班级与任务完成"
```
（用户执行）
