# P4 · 教师端预警看板与学员档案 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 教师实时看到进行中的动作对比会话与低分/异常记录（预警），并按班级查看学员档案（任务数、记录数、平均分）与逐条记录。

**Architecture:** 新增 `backend/routers/dashboard.py`（两个 router：`/api/dashboard` 看板、`/api/teacher` 档案）；数据全部来自已有表与内存会话管理器，无新表。前端教师导航加「预警看板」「学员档案」两页，看板 3 秒自动刷新。

**Tech Stack:** FastAPI + MySQL + 原生 JS SPA（无新依赖）。

## Global Constraints

- 仅教师角色（admin / elderly_service_teacher）可访问；普通教师只能看自己班级的数据。
- 预警规则：总分 < 60，或含遗漏步，或含顺序错误步。
- 看板「进行中」只列 assess 模式会话（普通监控会话不展示）。
- 学员档案记录只展示任务型练习（task_id 非空），避免自由练习混入教师档案。
- 姓名在教师端可不脱敏（教师管理自己学员），沿用现有列表的 mask 逻辑与否均可；本计划教师端显示明文（与班级成员页一致）。
- 本沙箱对 `.git` 无写权限：commit 由用户执行。

## File Structure

- Create `backend/routers/dashboard.py`（看板 + 档案两个 router）
- Modify `backend/main.py`：注册
- Modify `frontend/js/app.js`：教师导航「预警看板」「学员档案」两页

---

### Task 1: 后端接口

**Files:**
- Create: `backend/routers/dashboard.py`
- Modify: `backend/main.py`

**Interfaces:**
- Produces:
  - `GET /api/dashboard/live` → `{sessions:[...], alerts:[...]}`
  - `GET /api/teacher/students?class_id=` → `{items:[学员+统计]}`
  - `GET /api/teacher/students/{uid}/records` → `{items:[练习记录]}`

- [ ] **Step 1: 创建 dashboard.py**

```python
"""教师端：实时预警看板 + 学员档案聚合"""
from fastapi import APIRouter, Depends, HTTPException

from backend import database as db
from backend.vision.monitor_session import manager
from .auth_router import current_user
from .tasks import TEACHER_ROLES, can_manage

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
teacher_router = APIRouter(prefix="/api/teacher", tags=["teacher"])


def _teacher_only(user):
    if user["role"] not in TEACHER_ROLES:
        raise HTTPException(status_code=403, detail="无权限")


def _segment_counts(row):
    segs = db.json_load(row.get("segments") or "[]")
    return (sum(1 for s in segs if s.get("result") == "missed"),
            sum(1 for s in segs if s.get("result") == "order_error"))


@router.get("/live")
def live(user=Depends(current_user)):
    _teacher_only(user)
    sessions = []
    for sid in manager.list_sessions():
        sess = manager.get(sid)
        if sess is None or not sess.assess or sess._running is False:
            continue
        st = sess.get_state()
        u = db.query_one("SELECT name FROM users WHERE id = ?", (sess.user_id,))
        class_name = task_name = ""
        if sess.task_id:
            t = db.query_one(
                "SELECT t.title, c.name AS class_name FROM tasks t "
                "JOIN classes c ON t.class_id = c.id WHERE t.id = ?",
                (sess.task_id,))
            if t:
                task_name, class_name = t["title"], t["class_name"]
        sessions.append({
            "session_id": sid,
            "user_name": (u or {}).get("name", "") if sess.user_id else "自由练习",
            "task_name": task_name, "class_name": class_name,
            "process_name": sess.process.get("name", ""),
            "score": st.get("score"), "current_step": st.get("current_step"),
        })

    rows = db.query(
        "SELECT c.id, c.score, c.segments, c.started_at, u.name AS user_name, "
        "t.title AS task_name, cl.name AS class_name "
        "FROM motion_capture_records c "
        "LEFT JOIN users u ON c.user_id = u.id "
        "LEFT JOIN tasks t ON c.task_id = t.id "
        "LEFT JOIN classes cl ON t.class_id = cl.id "
        "ORDER BY c.started_at DESC LIMIT 50")
    alerts = []
    for r in rows:
        missed, order_err = _segment_counts(r)
        if (r["score"] is not None and r["score"] < 60) or missed or order_err:
            alerts.append({
                "record_id": r["id"], "user_name": r["user_name"] or "",
                "class_name": r["class_name"] or "", "task_name": r["task_name"] or "",
                "score": r["score"], "missed": missed, "order_errors": order_err,
                "started_at": r["started_at"],
            })
    return {"sessions": sessions, "alerts": alerts[:30]}

越权修正（必须实现）：上方的 /live 只适用于 admin 直通。普通教师必须只看到自己班级：
- sessions：无 task_id 的自由练习对普通教师跳过；有 task_id 时查班级 owner，非本人班级跳过。
- alerts：SQL 加 AND c.task_id <> ''，且普通教师追加 AND cl.owner_user_id = ?（admin 不加），查询参数随之变化。


def _visible_class(cid, user):
    cls = db.query_one("SELECT * FROM classes WHERE id = ?", (cid,))
    if not cls or not can_manage(user, cls["owner_user_id"]):
        raise HTTPException(status_code=403, detail="无权限")
    return cls


@teacher_router.get("/students")
def students(class_id: str, user=Depends(current_user)):
    _teacher_only(user)
    _visible_class(class_id, user)
    rows = db.query(
        "SELECT u.id, u.username, u.name, u.organization, "
        "(SELECT COUNT(*) FROM training_records tr WHERE tr.user_id = u.id) AS records_count, "
        "(SELECT COUNT(DISTINCT tr.task_id) FROM training_records tr "
        " WHERE tr.user_id = u.id AND tr.task_id <> '') AS tasks_done, "
        "(SELECT ROUND(AVG(tr.score), 1) FROM training_records tr "
        " WHERE tr.user_id = u.id) AS avg_score, "
        "(SELECT MAX(tr.created_at) FROM training_records tr "
        " WHERE tr.user_id = u.id) AS last_at "
        "FROM class_members m JOIN users u ON m.user_id = u.id "
        "WHERE m.class_id = ? ORDER BY u.created_at",
        (class_id,),
    )
    return {"items": rows}


@teacher_router.get("/students/{uid}/records")
def student_records(uid: str, user=Depends(current_user)):
    _teacher_only(user)
    if user["role"] != "admin":
        mine = db.query_one(
            "SELECT 1 AS x FROM class_members m "
            "JOIN classes c ON m.class_id = c.id "
            "WHERE m.user_id = ? AND c.owner_user_id = ?",
            (uid, user["id"]))
        if not mine:
            raise HTTPException(status_code=403, detail="该学员不在你的班级中")
    rows = db.query(
        "SELECT c.id, c.score, c.started_at, c.segments, "
        "p.name AS process_name, t.title AS task_name "
        "FROM motion_capture_records c "
        "LEFT JOIN processes p ON c.process_id = p.id "
        "LEFT JOIN tasks t ON c.task_id = t.id "
        "WHERE c.user_id = ? AND c.task_id <> '' "
        "ORDER BY c.started_at DESC LIMIT 100",
        (uid,),
    )
    for r in rows:
        r["missed"], r["order_errors"] = _segment_counts(r)
        r.pop("segments", None)
    return {"items": rows}
```

- [ ] **Step 2: main.py 注册**

import 列表加 `dashboard`；追加：

```python
app.include_router(dashboard.router)
app.include_router(dashboard.teacher_router)
```

- [ ] **Step 3: 语法检查**

Run: `python -c "import backend.main"`
Expected: 无报错。

- [ ] **Step 4: Commit**

```bash
git add backend/routers/dashboard.py backend/main.py
git commit -m "feat(P4): 看板与学员档案接口"
```
（用户执行）

### Task 2: 前端两页

**Files:**
- Modify: `frontend/js/app.js`

**Interfaces:**
- Consumes: Task 1 接口
- Produces: 导航 keys `live`（预警看板）、`archive`（学员档案）

- [ ] **Step 1: 导航**

教师分支加两项：

```js
      base.push({ key: "live", ico: "🚨", label: "预警看板" });
      base.push({ key: "archive", ico: "📁", label: "学员档案" });
```

titles / views 相应加 `live`、`archive`。

- [ ] **Step 2: 预警看板页**

```js
  // ---------- 预警看板 ----------
  async renderLive() {
    const c = document.getElementById("content");
    c.innerHTML = `<div class="card"><div class="card-head"><h3>进行中练习</h3></div>
      <div id="live-sessions">加载中…</div></div>
      <div class="card"><div class="card-head"><h3>预警（总分<60 / 遗漏 / 顺序错误）</h3></div>
      <div id="live-alerts">加载中…</div></div>`;
    await this.refreshLive();
    if (this._liveTimer) clearInterval(this._liveTimer);
    this._liveTimer = setInterval(() => this.refreshLive(), 3000);
  },
  async refreshLive() {
    try {
      const d = await this.api("/api/dashboard/live");
      const sEl = document.getElementById("live-sessions");
      if (sEl) sEl.innerHTML = d.sessions.length ? d.sessions.map(s => `
        <div class="step-item"><span class="order">${s.user_name.charAt ? s.user_name.charAt(0) : "?"}</span>
          <span>${this.esc(s.user_name)}${s.class_name ? " · " + this.esc(s.class_name) : ""}
          ${s.task_name ? " · " + this.esc(s.task_name) : ""}</span>
          <span style="margin-left:auto">${this.esc(s.process_name)} · 完成度 ${s.score ?? "--"}%</span></div>`).join("")
        : this.emptyHtml("暂无进行中的练习");
      const aEl = document.getElementById("live-alerts");
      if (aEl) aEl.innerHTML = d.alerts.length ? d.alerts.map(a => `
        <div class="step-item ${a.missed ? "miss" : a.order_errors ? "miss" : ""}">
          <span>${this.esc(a.user_name)}${a.class_name ? " · " + this.esc(a.class_name) : ""}
          ${a.task_name ? " · " + this.esc(a.task_name) : ""}</span>
          <span style="margin-left:auto;font-size:12px">
            得分 ${a.score ?? "--"}｜遗漏 ${a.missed}｜顺序错 ${a.order_errors}
            <button class="btn btn-sm" onclick="App.renderAssessmentReport('${a.record_id}')">报告</button></span>
        </div>`).join("")
        : this.emptyHtml("暂无预警记录");
    } catch (e) {}
  },
```

`go()` 离开看板时清理定时器：`if (view !== "live" && this._liveTimer) { clearInterval(this._liveTimer); this._liveTimer = null; }`

- [ ] **Step 3: 学员档案页**

```js
  // ---------- 学员档案 ----------
  async renderArchive() {
    const c = document.getElementById("content");
    const cls = await this.api("/api/classes");
    c.innerHTML = `<div class="card"><div class="card-head"><h3>学员档案</h3>
      <select id="archive-class" onchange="App.loadArchive()">
        <option value="">请选择班级</option>
        ${cls.items.map(x => `<option value="${x.id}">${this.esc(x.name)}</option>`).join("")}
      </select></div>
      <div id="archive-list">${this.emptyHtml("先选择班级")}</div></div>`;
  },
  async loadArchive() {
    const cid = document.getElementById("archive-class").value;
    const box = document.getElementById("archive-list");
    if (!cid) { box.innerHTML = this.emptyHtml("先选择班级"); return; }
    const d = await this.api("/api/teacher/students?class_id=" + cid);
    box.innerHTML = `<table><thead><tr><th>学员</th><th>任务数</th><th>练习次数</th><th>平均分</th><th>最近练习</th><th>操作</th></tr></thead>
      <tbody>${d.items.map(s => `<tr>
        <td><b>${this.esc(s.name)}</b>（${this.esc(s.username)}）</td>
        <td>${s.tasks_done}</td><td>${s.records_count}</td>
        <td>${s.avg_score == null ? "--" : s.avg_score}</td>
        <td>${s.last_at ? new Date(s.last_at * 1000).toLocaleString() : "--"}</td>
        <td><button class="btn btn-sm btn-primary" onclick="App.loadStudentRecords('${s.id}')">查看记录</button></td>
      </tr>`).join("") || `<tr><td colspan="6">${this.emptyHtml("班级暂无学员")}</td></tr>`}</tbody></table>
      <div id="student-records"></div>`;
  },
  async loadStudentRecords(uid) {
    const box = document.getElementById("student-records");
    const d = await this.api("/api/teacher/students/" + uid + "/records");
    box.innerHTML = `<div style="margin-top:14px"><h4 style="margin-bottom:8px">练习明细</h4>
      <table><thead><tr><th>任务</th><th>流程</th><th>得分</th><th>遗漏</th><th>顺序错误</th><th>时间</th><th>操作</th></tr></thead>
      <tbody>${d.items.map(r => `<tr>
        <td>${this.esc(r.task_name || "-")}</td><td>${this.esc(r.process_name || "-")}</td>
        <td>${r.score == null ? "--" : r.score}</td><td>${r.missed}</td><td>${r.order_errors}</td>
        <td>${new Date((r.started_at || 0) * 1000).toLocaleString()}</td>
        <td><button class="btn btn-sm" onclick="App.renderAssessmentReport('${r.id}')">报告</button></td>
      </tr>`).join("") || `<tr><td colspan="7">${this.emptyHtml("暂无练习记录")}</td></tr>`}</tbody></table></div>`;
  },
```

- [ ] **Step 4: 语法检查**

Run: `node --check frontend/js/app.js`
Expected: 退出码 0。

- [ ] **Step 5: Commit**

```bash
git add frontend/js/app.js
git commit -m "feat(P4): 预警看板与学员档案页面"
```
（用户执行）

### Task 3: 集成验收

**Files:** 无

- [ ] **Step 1: 全量自检 + API 冒烟**

启动服务；admin 建班级 → 加 nurse001 → 发任务；直接向 MySQL 插入一条 nurse001 的任务练习记录（score=50、segments 含 1 missed），然后：
- `GET /api/dashboard/live` → alerts 应包含该记录；
- `GET /api/teacher/students?class_id=` → nurse001 统计 records_count=1、avg_score=50；
- `GET /api/teacher/students/{uid}/records` → 返回该条；
最后清理全部冒烟数据（记录/任务/班级）。

- [ ] **Step 2: Commit**

```bash
git add -A
git commit -m "feat(P4): 教师端看板与档案完成"
```
（用户执行）
