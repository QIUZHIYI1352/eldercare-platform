# P3 · 学员端闭环 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 学员从「我的任务」进入任务 → 摄像头做流程（P1 对比引擎）→ 结束自动生成培训记录与对比报告 → 学员可在「个人记录」回看本人记录与报告。

**Architecture:** 后端把 P1 的 `POST /api/assessment/sessions` 扩展为「task_id 模式」（服务端校验任务归属/成员关系并由任务解析流程）；`MonitorSession._save_record` 在有 `task_id` 时同步写一条 `training_records`；培训记录接口按角色过滤并暴露 `capture_record_id` 供跳转报告。前端「我的任务」卡片加「开始练习」，练习页绑定任务；「培训记录」对学员显示为「个人记录」。

**Tech Stack:** FastAPI + PyMySQL + 原生 JS SPA（沿用既有结构，无新依赖）。

## Global Constraints

- `POST /api/assessment/sessions` 兼容两种模式：无 `task_id` = 自由选择流程（P1 行为不变）；有 `task_id` = 服务端用任务绑定的流程，忽略客户端 process_id，且仅任务所在班级的学员可用，任务必须 `published`。
- 每次任务练习结束写一条 `training_records`（task_id、capture_record_id、score、完成/漏步列表、duration），允许多次练习产生多条历史。
- `training_records` 需要 duration：用会话真实起止墙钟差，不能用同一秒的 started_at/ended_at。
- 培训记录接口：教师/admin 看全部（姓名脱敏），学员只看本人记录。
- 学员报告跳转仍走 P1 assessment 报告的权限（本人可看）。
- 本沙箱对 `.git` 无写权限：commit 由用户执行。

## File Structure

- Modify `backend/routers/assessment.py`：task_id 模式校验与流程解析。
- Modify `backend/vision/monitor_session.py`：记录真实时长；有 task_id 时同步写 training_records。
- Modify `backend/routers/training.py`：角色过滤 + capture_record_id 暴露 + 本人接口。
- Modify `frontend/js/app.js`：我的任务「开始练习」、练习页任务模式、个人记录展示。

---

### Task 1: 后端任务绑定与记录生成

**Files:**
- Modify: `backend/routers/assessment.py`
- Modify: `backend/vision/monitor_session.py`
- Modify: `backend/routers/training.py`

**Interfaces:**
- Produces:
  - `POST /api/assessment/sessions`：`{task_id, source}`（任务模式）→ `{session_id, process_id, process_name}`
  - `MonitorSession.record`：新增 `duration` 字段
  - `GET /api/training/records`：按角色过滤；条目含 `capture_record_id`

- [ ] **Step 1: assessment.py start_session 支持任务模式**

把现有 `start_session` 的流程解析部分改为：

```python
@router.post("/sessions")
def start_session(payload: dict, user=Depends(current_user)):
    source = (payload.get("source") or "0").strip()
    process_id = payload.get("process_id") or ""
    task_id = payload.get("task_id") or ""
    if task_id:
        # 任务模式：服务端校验归属并解析流程，忽略客户端 process_id
        task = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not task or task.get("status") != "published":
            raise HTTPException(status_code=400, detail="任务不存在或未发布")
        if user["role"] not in STUDENT_ROLES:
            raise HTTPException(status_code=403, detail="仅学员可执行任务")
        member = db.query_one(
            "SELECT 1 AS x FROM class_members WHERE class_id=? AND user_id=?",
            (task["class_id"], user["id"]))
        if not member:
            raise HTTPException(status_code=403, detail="你不在该任务班级中")
        process = db.query_one("SELECT * FROM processes WHERE id = ?",
                               (task["process_id"],))
        if not process:
            raise HTTPException(status_code=400, detail="任务绑定的流程不存在")
    else:
        process = db.query_one("SELECT * FROM processes WHERE id = ?", (process_id,))
        if not process:
            raise HTTPException(status_code=400, detail="请选择有效的护理流程")
    process["steps"] = db.json_load(process.get("steps"))
    actions = _load_actions()
    try:
        sid = manager.start(source, process, actions, user["id"], task_id, assess=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"动作对比启动失败: {e}")
    auth.audit(user, "start_assessment", f"动作对比 流程={process['name']} 源={source}")
    return {"session_id": sid, "process_id": process["id"],
            "process_name": process["name"]}
```

并在文件顶部 import 区加 `from .tasks import STUDENT_ROLES`。

- [ ] **Step 2: MonitorSession 记录真实时长并写 training_records**

1) `_save_record` 开头（`db.now()` 落库前）记录：

```python
        duration = max(1, int(time.time() - self._start_wall))
```

2) `self.record` 字典加 `"duration": duration`。
3) 落库 `motion_capture_records` 的 `started_at`/`ended_at` 改为 `(int(self._start_wall), int(time.time()))`，避免同秒。
4) 插入完成后追加：

```python
        if self.task_id:
            matched = [s["order"] for s in segments if s["result"] == "matched"]
            missed = [s["order"] for s in segments
                      if s["result"] in ("missed", "order_error")]
            trid = db.gen_id("train")
            db.execute(
                "INSERT INTO training_records "
                "(id, user_id, process_id, task_id, capture_record_id, score, "
                "completed_steps, missed_steps, duration, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (trid, self.user_id, self.process.get("id", ""), self.task_id, rid,
                 score if score is not None else 0.0,
                 db.json_dump(matched), db.json_dump(missed),
                 duration, db.now()),
            )
```

（segments/matched 等变量在 _save_record 内已有作用域，无需重复构建。）

- [ ] **Step 3: training.py 角色过滤与字段**

把 `list_records` 改为：

```python
@router.get("/records")
def list_records(user=Depends(current_user)):
    if user["role"] in ("admin", "elderly_service_teacher"):
        where = ""
    else:
        where = "WHERE t.user_id = ?"
    rows = db.query(
        """SELECT t.*, p.name AS process_name, u.name AS user_name
           FROM training_records t
           LEFT JOIN processes p ON t.process_id = p.id
           LEFT JOIN users u ON t.user_id = u.id
           {where} ORDER BY t.created_at DESC LIMIT 200""".format(where=where),
        (user["id"],) if where else (),
    )
    for r in rows:
        r["completed_steps"] = db.json_load(r.get("completed_steps"))
        r["missed_steps"] = db.json_load(r.get("missed_steps"))
        r["user_name"] = auth.mask_info(r.get("user_name") or "")
    return {"items": rows}
```

注意：`create_record` 保持不动（老接口兼容），但新数据一律由 MonitorSession 写入，避免前端重复造记录。

- [ ] **Step 4: 语法与自检**

Run: `python -c "import backend.main"` 与既有三个自检
Expected: 无报错且三个 OK。

- [ ] **Step 5: Commit**

```bash
git add backend/routers/assessment.py backend/vision/monitor_session.py backend/routers/training.py
git commit -m "feat(P3): 任务绑定采集与自动生成培训记录"
```
（用户执行）

### Task 2: 前端任务练习与个人记录

**Files:**
- Modify: `frontend/js/app.js`

**Interfaces:**
- Produces:
  - `App.startTaskPractice(taskId)`：记住任务并进入练习页
  - `renderAssessment`：任务模式下隐藏流程选择、显示任务标题、结束后可回「我的任务」
  - `startAssessment`：有任务时只发 `{task_id, source}`
  - `renderTraining`：学员视图显示「个人记录」，列表含报告入口

- [ ] **Step 1: 我的任务加「开始练习」**

`renderMyTasks` 的卡片按钮区改为：

```js
        <p style="color:var(--muted);font-size:13px">截止：${new Date((t.deadline || 0) * 1000).toLocaleString()}</p>
        <div style="margin-top:10px">
          <button class="btn btn-primary btn-sm" onclick="App.startTaskPractice('${t.id}')">开始练习</button>
          ${t.latest_score == null ? "" : `<button class="btn btn-sm" style="margin-left:8px" onclick="App.go('training')">查看记录</button>`}
        </div>
```

并新增：

```js
  startTaskPractice(taskId) {
    this.state.taskId = taskId;
    this.state.taskTitle = (window._myTasks || []).find(t => t.id === taskId)?.title || "任务练习";
    this.go("assessment");
  },
```

`renderMyTasks` 开头把列表缓存起来：`window._myTasks = data.items;`

- [ ] **Step 2: 练习页任务模式**

`renderAssessment()` 开头（HTML 模板内）根据任务模式调整：

```js
    const taskMode = !!this.state.taskId;
    const title = taskMode ? `任务练习：${this.esc(this.state.taskTitle || "")}` : "动作对比（摄像头采集 → 自动评分）";
```

HTML 中的 h3 改为 `${title}`；流程选择行的外层包一层：

```js
          <div class="form-row" style="margin:0;${taskMode ? "display:none" : ""}"><label>护理流程</label>
            <select id="as-process">${procOpts}</select></div>
```

`startAssessment` 的 body 改为：

```js
      const body = this.state.taskId
        ? { task_id: this.state.taskId, source }
        : { source, process_id: processId };
      const data = await this.api("/api/assessment/sessions", "POST", body);
```

（task 模式同样必须有 processId 变量，但不在 UI 展示；把 `const processId = ...` 保留并从隐藏 select 读取即可。）

`stopAssessment` 落库跳报告后，报告页返回按钮按来源切换：`renderAssessmentReport` 顶部记 `window._reportFrom = this.state.taskId ? "mytasks" : "assessment"`，返回按钮 `onclick="App.go(window._reportFrom)"`，按钮文字用 `window._reportFrom === "mytasks" ? "返回我的任务" : "返回"`。

练习结束后清空任务状态（回到列表再进入自由模式不串场）：在 `stopAssessment` 开头保留 taskId，结束时若 taskId 存在则 `this.state.taskId = null; this.state.taskTitle = "";`，但跳报告前先存到 `window._lastTaskReport = taskId` 供报告页返回判断。

简化实现（推荐）：报告返回判断只依赖 `window._reportFrom`，自由模式进报告置 `"assessment"`，任务模式进报告置 `"mytasks"`。

- [ ] **Step 3: 培训记录/个人记录**

`navItems()` 中把 training 项按角色改名并只让学员与教师可见保持不变（现对所有角色可见，本步改为角色化）：

```js
      { key: "training", ico: "🎓", label: isStudent ? "个人记录" : "培训记录" },
```

`renderTraining` 表格在「操作」列追加报告入口：

```js
        <td>${t.capture_record_id ? `<button class="btn btn-sm btn-primary" onclick="App.renderAssessmentReport('${t.capture_record_id}')">查看报告</button>` : ""}</td>
```

并把表头 `学员` 列在学员视角隐藏（学员看自己，无意义）——直接保留亦可（值为本人），为少改动保留该列。

- [ ] **Step 4: 语法检查**

Run: `node --check frontend/js/app.js`
Expected: 无输出且退出码 0。

- [ ] **Step 5: Commit**

```bash
git add frontend/js/app.js
git commit -m "feat(P3): 学员任务练习与个人记录页面"
```
（用户执行）

### Task 3: 集成验收

**Files:** 无

- [ ] **Step 1: 全量自检**

Run: 三个 `python -m tests.test_*` + `node --check`
Expected: 全部 OK。

- [ ] **Step 2: API 冒烟（无摄像头）**

1. admin 建冒烟班级 → 加 nurse001 → 发任务（记 id）。
2. nurse001 登录 `POST /api/assessment/sessions {task_id, source:"0"}` → 返回 session_id（若本机无摄像头，线程稍后进入 error 状态，不落 training 记录，属预期）。
3. 用「不属于该班级的学员」elderly001 调同一接口 → Expected 403。
4. `GET /api/training/records`（nurse001）只能看到本人记录（当前为空列表）。
5. 清理冒烟班级/任务。

- [ ] **Step 3: 用户手动验收（有摄像头）**

网页：nurse001 登录 → 我的任务 → 开始练习 → 做完整流程（需流程步骤已绑序列模板）→ 自动跳报告 → 回个人记录看到该次得分并可点开报告。

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(P3): 学员端闭环完成"
```
（用户执行）
