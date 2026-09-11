# P1 · 对比引擎（采集落库 + 逐段评分 + 遗漏/顺序错误）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 MySQL 版平台上实现「动作对比」闭环：实时会话采集逐帧骨骼特征并自动切步，结束落库；对比逻辑区分完成/遗漏/顺序错误；报告给出总分 + 每步得分；支持修改步边界后重算。

**Architecture:** 纯算法放新增 `backend/vision/assessment.py`（不依赖摄像头/DB，可单测）；`MonitorSession` 增加 `assess` 模式做采集与自动切步、结束自动落库；新增 `backend/routers/assessment.py` 提供会话与报告接口；前端新增「动作对比」页与报告页。普通监控模式行为保持不变。

**Tech Stack:** Python + FastAPI + PyMySQL（沿用 P0 数据层）+ 原生 JS SPA + numpy（已有）。

## Global Constraints

- 现有 API 与普通监控模式行为不允许回归：`MonitorSession` 默认 `assess=False` 时逻辑与 P0 前完全一致（含用户未提交的中文叠加改动，必须保留）。
- 新表 `motion_capture_records` 用 MySQL 方言 DDL：主键 `VARCHAR(64)`、TEXT 列不带字面 DEFAULT。
- 采集只存 16 维特征向量 + 相对时间戳（`frames` JSON：`{"ts": [...], "feats": [[...]]}`），不存视频、不存 33 关键点。
- 标准 = 动作的序列模板（`template_type='sequence'` 且 `vectors` 长度 ≥ 2）；规则型动作无标准时，该步显示「无标准」且不计入总分，但仍参与遗漏/顺序错误分类。
- 对比分类用「带占用标记的贪心顺序匹配」（不用现有 LCS）：匹配成功=完成；执行过但未排入正确位置=顺序错误；动作从未出现或次数不足=遗漏。
- 每步质量分 = `100 × (1 - min(dtw_dist / len(模板) / threshold, 1))`，clamp 0~100；顺序错误/遗漏在有模板时计 0 分；总分 = 有模板步骤的平均分，无任何有模板步骤时 `score = NULL`。
- 报告页边界编辑以「秒」为单位提交，服务端用帧时间戳就近换算索引后重算。
- 本沙箱对 `.git` 无写权限：每任务末尾的 commit 步骤由用户在自己的终端执行；执行代理不因 commit 失败中断。

## File Structure

- Modify `backend/database.py`：`_DDL` 增加 `motion_capture_records`。
- Create `backend/vision/assessment.py`：贪心分类 + DTW 分段评分 + 报告组装（纯函数）。
- Create `tests/test_assessment.py`：核心算法自检。
- Modify `backend/vision/monitor_session.py`：`assess` 模式采集、自动切步、自动结束、落库。
- Create `backend/routers/assessment.py`：会话/记录/报告/边界重算接口。
- Modify `backend/main.py`：注册 assessment 路由。
- Modify `frontend/js/app.js`：导航「动作对比」、会话页、报告页。

---

### Task 1: 对比核心算法（纯函数 + 自检）

**Files:**
- Create: `backend/vision/assessment.py`
- Create: `tests/test_assessment.py`

**Interfaces:**
- Produces:
  - `classify_steps(step_actions: list[str], events: list[dict]) -> list[dict]`
    返回等长结果：`{"order","action_id","result","event_index"}`，result ∈ matched/order_error/missed。
  - `segment_score(segment_vectors, template_vectors, threshold) -> float|None`（0~100，保留 1 位）
  - `build_assessment(steps, actions_map, events, frames) -> (steps_snapshot, segments, score)`
    - `frames = {"ts": [float], "feats": [[float×16], ...]}`
    - segments 每条：`{"order","name","action_id","result","event_index","start_idx","end_idx","start_ts","end_ts","dist","score","comparable"}`
    - 返回的 steps_snapshot = `[{"order","name","action_id"}]`

- [ ] **Step 1: 写失败测试**

Create `tests/test_assessment.py`：

```python
"""P1 对比算法自检：分类 + 分段评分（无需摄像头/MySQL）"""
from backend.vision.assessment import classify_steps, segment_score


def ev(action_id, start_idx=0, end_idx=9):
    return {"action_id": action_id, "start_idx": start_idx, "end_idx": end_idx}


def results(step_actions, events):
    return [r["result"] for r in classify_steps(step_actions, events)]


def test_correct_order():
    assert results(["A", "B", "C"], [ev("A", 0, 9), ev("B", 10, 19), ev("C", 20, 29)]) \
        == ["matched", "matched", "matched"]


def test_missing_step():
    assert results(["A", "B", "C"], [ev("A"), ev("B")]) \
        == ["matched", "matched", "missed"]


def test_out_of_order():
    # 标准 A,B,C；实际 B,A,C：C 顺序正确，B 先于 A 执行 → B 判顺序错误
    assert results(["A", "B", "C"], [ev("B", 0, 9), ev("A", 10, 19), ev("C", 20, 29)]) \
        == ["matched", "order_error", "matched"]


def test_duplicate_step_missing_occurrence():
    # 标准 A,B,A；只做了一次 A + B：第二次 A 应判遗漏，不是顺序错误
    assert results(["A", "B", "A"], [ev("A", 0, 9), ev("B", 10, 19)]) \
        == ["matched", "matched", "missed"]


def test_duplicate_step_out_of_order():
    # 标准 A,B,A；实际 A,A,B：第二个 A 出现在 B 前 → 第三步 A 判顺序错误
    assert results(["A", "B", "A"], [ev("A", 0, 9), ev("A", 10, 19), ev("B", 20, 29)]) \
        == ["matched", "matched", "order_error"]


def test_segment_score_same_and_far():
    template = [[0.0] * 4, [1.0] * 4, [2.0] * 4, [3.0] * 4]
    same = segment_score(template, template, threshold=8.0)
    assert same is not None and same >= 99.0
    far = segment_score([[50.0] * 4] * 4, template, threshold=8.0)
    assert far == 0.0


if __name__ == "__main__":
    test_correct_order()
    test_missing_step()
    test_out_of_order()
    test_duplicate_step_missing_occurrence()
    test_duplicate_step_out_of_order()
    test_segment_score_same_and_far()
    print("OK: assessment logic checks passed")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m tests.test_assessment`
Expected: `ModuleNotFoundError: No module named 'backend.vision.assessment'`

- [ ] **Step 3: 实现 assessment.py**

Create `backend/vision/assessment.py`：

```python
"""对比评估核心逻辑：步骤分类 + 每步 DTW 质量分。

标准步骤序列 vs 实际识别事件序列采用「带占用标记的贪心顺序匹配」：
按标准顺序为每步寻找其动作的下一个未占用事件 → matched；
动作执行过但没排进正确位置 → order_error；动作从未出现或次数不足 → missed。
不用 LCS：种子流程里同一动作会重复出现，LCS 无法区分「缺一次执行」和「乱序」。
"""
import numpy as np

from backend.vision.template_matcher import dtw_distance


def classify_steps(step_actions, events):
    """events: [{action_id, start_idx, end_idx}, ...]（按时间升序）"""
    m = len(events)
    used = [False] * m
    ptr = 0
    outcomes = []
    for order, aid in enumerate(step_actions, 1):
        ev_idx = None
        for j in range(ptr, m):
            if not used[j] and events[j]["action_id"] == aid:
                ev_idx = j
                break
        if ev_idx is not None:
            used[ev_idx] = True
            ptr = ev_idx + 1
            outcomes.append({"order": order, "action_id": aid,
                             "result": "matched", "event_index": ev_idx})
        else:
            outcomes.append({"order": order, "action_id": aid,
                             "result": "pending", "event_index": None})

    for o in outcomes:
        if o["result"] != "pending":
            continue
        ev_idx = None
        for j in range(m):
            if not used[j] and events[j]["action_id"] == o["action_id"]:
                ev_idx = j
                break
        if ev_idx is not None:
            used[ev_idx] = True
            o["result"] = "order_error"
            o["event_index"] = ev_idx
        else:
            o["result"] = "missed"
    return outcomes


def segment_score(segment_vectors, template_vectors, threshold=8.0):
    """DTW 距离按模板长度归一后映射为 0~100 质量分。"""
    s1 = np.asarray(segment_vectors, dtype=np.float32)
    s2 = np.asarray(template_vectors, dtype=np.float32)
    if len(s1) < 1 or len(s2) < 2:
        return None
    dist = dtw_distance(s1, s2) / len(s2)
    score = 100.0 * (1.0 - min(dist / max(float(threshold), 1e-9), 1.0))
    return round(max(0.0, score), 1)


def _template_of(action):
    """取动作的序列模板向量；无标准返回 []。"""
    if not action or action.get("template_type") != "sequence":
        return []
    td = action.get("template_data") or {}
    vectors = td.get("vectors") or []
    return vectors if len(vectors) >= 2 else []


def build_assessment(steps, actions_map, events, frames):
    """组装报告数据。

    steps: [{order, name, action_id}]（流程步骤快照）
    actions_map: {action_id: action}（含 template_type/template_data，由调用方从库中加载）
    events: [{action_id, start_idx, end_idx}]，索引指向 frames["feats"]
    frames: {"ts": [...], "feats": [[16 维], ...]}
    返回 (steps_snapshot, segments, score)。
    """
    step_actions = [s["action_id"] for s in steps]
    outcomes = classify_steps(step_actions, events)
    ts = frames.get("ts") or []
    feats = frames.get("feats") or []

    scored = []
    segments = []
    for step, outcome in zip(steps, outcomes):
        action = actions_map.get(step["action_id"]) or {}
        template = _template_of(action)
        td = action.get("template_data") or {}
        threshold = float(td.get("threshold", 8.0))
        ev_idx = outcome["event_index"]
        seg = {"order": step["order"], "name": step.get("name", ""),
               "action_id": step["action_id"], "result": outcome["result"],
               "event_index": ev_idx, "start_idx": None, "end_idx": None,
               "start_ts": None, "end_ts": None, "dist": None,
               "score": None, "comparable": bool(template)}

        if ev_idx is not None and 0 <= ev_idx < len(events):
            ev = events[ev_idx]
            start, end = ev["start_idx"], ev["end_idx"]
            seg["start_idx"], seg["end_idx"] = start, end
            if 0 <= start < len(ts):
                seg["start_ts"] = round(ts[start], 2)
            if 0 <= end < len(ts):
                seg["end_ts"] = round(ts[end], 2)

        if template:
            if outcome["result"] == "matched" and ev_idx is not None:
                segment = feats[seg["start_idx"]:seg["end_idx"] + 1]
                if len(segment) >= 1:
                    seg["score"] = segment_score(segment, template, threshold)
                    seg["dist"] = round(
                        float(dtw_distance(
                            np.asarray(segment, dtype=np.float32),
                            np.asarray(template, dtype=np.float32)) / len(template)), 2)
            else:
                seg["score"] = 0.0
            scored.append(seg["score"] if seg["score"] is not None else 0.0)
        segments.append(seg)

    score = round(sum(scored) / len(scored), 1) if scored else None
    snapshot = [{"order": s["order"], "name": s.get("name", ""),
                 "action_id": s.get("action_id", "")} for s in steps]
    return snapshot, segments, score
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m tests.test_assessment`
Expected: `OK: assessment logic checks passed`

- [ ] **Step 5: Commit**

```bash
git add backend/vision/assessment.py tests/test_assessment.py
git commit -m "feat(P1): 对比核心算法（贪心分类 + DTW 分段评分）"
```
（用户在自己的终端执行）

### Task 2: 数据表 + MonitorSession assess 模式

**Files:**
- Modify: `backend/database.py`
- Modify: `backend/vision/monitor_session.py`

**Interfaces:**
- Consumes: Task 1 的 `build_assessment`；P0 的 `db.execute` / `db.gen_id` / `db.now` / `db.json_dump`
- Produces: `manager.start(source, process, actions, user_id="", task_id="", assess=False)`；session 结束后 `sess.record_id` 非空、`sess.record`（含 report 数据）；`sess.wait_finished(timeout)`。

- [ ] **Step 1: database.py 增加表**

在 `_DDL` 末尾（`audit_logs` 的 `;` 后）追加：

```sql

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
```

- [ ] **Step 2: MonitorSession 支持 assess 模式**

对 `backend/vision/monitor_session.py` 做以下增量修改（保留现有中文叠加等用户改动）：

1) 文件头部 `import time` 之后增加 `from backend import database as db`。

2) `__init__` 签名改为：

```python
    def __init__(self, source, process, actions, user_id="", task_id="", assess=False):
        self.user_id = user_id
        self.task_id = task_id
        self.assess = assess
        self.record_id = None
        self.record = None
        self._finish_at = 0.0
        self._rule_state = {a["id"]: {"begin": None, "first_active": None, "active": False}
                            for a in actions if a.get("template_type") != "sequence"}
        if assess:
            self._frames = {"ts": [], "feats": []}
            self._events = []
```

3) `_run` 中，`ok_pose, pts = engine.landmarks_from_frame(frame)` 之后的帧处理调整为：

```python
            active = []
            if ok_pose:
                features = PoseEngine.compute_features(pts)
                if self.assess:
                    self._frames["ts"].append(round(time.time() - self._start_wall, 3))
                    self._frames["feats"].append(features_to_vector(features).tolist())
                active = recognizer.update(features, self.rule_actions)
                vec = features_to_vector(features)
                # 序列模板匹配循环保持不变（命中时追加事件，见第 5 步）
                # 规则型动作事件必须在 active 更新为当前帧之后记录：
                if self.assess:
                    self._capture_rule_events(features, active)
```

（注意：落库帧追加必须发生在 `recognizer.update` 之前，规则事件记录必须发生在 `active` 更新为当前帧之后。）

4) 在类中新增 `_frame_idx` 与 `_capture_rule_events` 两个方法：

```python
    def _frame_idx(self):
        return len(self._frames["ts"]) - 1

    def _capture_rule_events(self, features, active):
        """规则型动作：满足起始帧 → 离开帧 作为一个事件。"""
        from backend.vision.action_recognizer import evaluate_conditions
        idx = self._frame_idx()
        active_set = set(active)
        for aid, st in self._rule_state.items():
            action = self.action_map[aid]
            satisfied = evaluate_conditions(features, action.get("conditions") or [])
            if satisfied and st["begin"] is None:
                st["begin"] = idx
            if not satisfied:
                st["begin"] = None
            if aid in active_set and not st["active"]:
                st["active"] = True
                st["first_active"] = idx
            elif aid not in active_set and st["active"]:
                start = st["begin"] if st["begin"] is not None else st["first_active"]
                end = max(start, idx - 1)
                self._events.append({"action_id": aid, "start_idx": start, "end_idx": end})
                st["begin"] = None
                st["first_active"] = None
                st["active"] = False
```

5) 序列模板命中处（原代码 `if hit:` 块内）追加事件：

```python
                    if hit and self.assess:
                        idx = self._frame_idx()
                        start = max(0, idx - m.template_len + 1)
                        self._events.append({"action_id": aid, "start_idx": start, "end_idx": idx})
```

6) `_run` 循环内、`self._set_state(...)` 之后追加自动结束逻辑：

```python
            if self.assess and result["score"] >= 100 and not self._finish_at:
                self._finish_at = time.time() + 1.2
            if self._finish_at and time.time() >= self._finish_at:
                break
```

7) `_run` 的循环退出后、`cap.release()` 之前调用落库：

```python
        if self.assess:
            self._save_record()
```

8) 新增落库方法：

```python
    def _save_record(self):
        from backend.vision.assessment import build_assessment
        events = sorted(self._events, key=lambda e: (e["start_idx"], e["end_idx"]))
        actions_map = {a["id"]: a for a in self.actions}
        snapshot, segments, score = build_assessment(
            self.steps, actions_map, events, self._frames)
        self.record = {"steps": snapshot, "segments": segments,
                       "score": score, "events": events}
        rid = db.gen_id("cap")
        db.execute(
            "INSERT INTO motion_capture_records "
            "(id, user_id, task_id, process_id, source, source_type, status, "
            "started_at, ended_at, steps, segments, frames, score, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, self.user_id, self.task_id, self.process.get("id", ""),
             str(self.source), "mediapipe", "finished",
             db.now(), db.now(),
             db.json_dump(snapshot), db.json_dump(segments),
             db.json_dump(self._frames), score, db.now()),
        )
        self.record_id = rid
        self._set_state(record_id=rid)
```

9) `start()` 初始化墙钟：

```python
    def start(self):
        self._start_wall = time.time()
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
```

10) `SessionManager.start` 增加参数透传：

```python
    def start(self, source, process, actions, user_id="", task_id="", assess=False):
        sid = "sess_" + uuid.uuid4().hex[:12]
        sess = MonitorSession(source, process, actions, user_id, task_id, assess)
        sess.start()
        with self._lock:
            self._sessions[sid] = sess
        return sid
```

11) 在 MonitorSession 上新增等待方法：

```python
    def wait_finished(self, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline and self._running:
            time.sleep(0.05)
        return self.record_id
```

- [ ] **Step 3: 状态与语法检查**

Run: `python -c "import backend.vision.monitor_session"` 与 `python -m tests.test_assessment`
Expected: 均无报错，第二个输出 `OK: assessment logic checks passed`

- [ ] **Step 4: Commit**

```bash
git add backend/database.py backend/vision/monitor_session.py
git commit -m "feat(P1): MonitorSession 采集模式与落库"
```
（用户在自己的终端执行）

### Task 3: assessment 路由

**Files:**
- Create: `backend/routers/assessment.py`
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `manager.start(...)`（Task 2）、`build_assessment`（Task 1）、P0 数据层
- Produces:
  - `POST /api/assessment/sessions` `{source, process_id, task_id?}` → `{session_id}`
  - `DELETE /api/assessment/sessions/{sid}` → `{record_id}`
  - `GET /api/assessment/records` → `{items:[摘要]}`
  - `GET /api/assessment/records/{rid}` → 报告详情（含 segments，不含 frames）
  - `PUT /api/assessment/records/{rid}/segments` `{segments:[{order,start_ts,end_ts}]}` → 重算后的报告详情

- [ ] **Step 1: 创建路由文件**

Create `backend/routers/assessment.py`：

```python
"""动作对比：采集会话 + 报告（总分、每步得分、遗漏/顺序错误）"""
import bisect

from fastapi import APIRouter, Depends, HTTPException

from backend import auth
from backend import database as db
from backend.vision.assessment import build_assessment
from backend.vision.monitor_session import manager
from .auth_router import current_user

router = APIRouter(prefix="/api/assessment", tags=["assessment"])

TEACHER_ROLES = {"admin", "elderly_service_teacher"}


def _load_actions():
    rows = db.query("SELECT * FROM actions ORDER BY created_at")
    for a in rows:
        a["conditions"] = db.json_load(a.get("conditions"))
        a["template_data"] = db.json_load(a.get("template_data"), {})
    return rows


def _actions_map_for(actions):
    return {a["id"]: a for a in actions}


@router.post("/sessions")
def start_session(payload: dict, user=Depends(current_user)):
    source = (payload.get("source") or "0").strip()
    process_id = payload.get("process_id") or ""
    task_id = payload.get("task_id") or ""
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
    return {"session_id": sid}


@router.delete("/sessions/{sid}")
def stop_session(sid: str, user=Depends(current_user)):
    sess = manager.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在或已结束")
    manager.stop(sid)
    record_id = sess.wait_finished(6.0)
    return {"record_id": record_id}


@router.get("/records")
def list_records(user=Depends(current_user)):
    if user["role"] in TEACHER_ROLES:
        where = ""
    else:
        where = "WHERE c.user_id = ?"
    rows = db.query(
        "SELECT c.id, c.user_id, u.name AS user_name, c.process_id, "
        "p.name AS process_name, c.score, c.status, c.started_at, c.ended_at, "
        "c.segments FROM motion_capture_records c "
        "LEFT JOIN users u ON c.user_id = u.id "
        "LEFT JOIN processes p ON c.process_id = p.id " + where +
        " ORDER BY c.started_at DESC LIMIT 200",
        (user["id"],) if where else (),
    )
    for r in rows:
        segs = db.json_load(r.pop("segments"))
        r["steps_total"] = len(segs)
        r["order_errors"] = sum(1 for s in segs if s.get("result") == "order_error")
        r["missed"] = sum(1 for s in segs if s.get("result") == "missed")
        r["user_name"] = auth.mask_info(r.get("user_name") or "")
    return {"items": rows}


@router.get("/records/{rid}")
def get_record(rid: str, user=Depends(current_user)):
    row = db.query_one("SELECT * FROM motion_capture_records WHERE id = ?", (rid,))
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    if user["role"] not in TEACHER_ROLES and row["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权查看该记录")
    row["steps"] = db.json_load(row.get("steps"))
    row["segments"] = db.json_load(row.get("segments"))
    row.pop("frames", None)
    if user["role"] not in TEACHER_ROLES:
        row.pop("user_id", None)
    return row


def _nearest_index(ts_list, target):
    if not ts_list:
        return 0
    import bisect
    i = bisect.bisect_left(ts_list, target)
    if i >= len(ts_list):
        return len(ts_list) - 1
    if i == 0:
        return 0
    return i if abs(ts_list[i] - target) < abs(ts_list[i - 1] - target) else i - 1


@router.put("/records/{rid}/segments")
def update_segments(rid: str, payload: dict, user=Depends(current_user)):
    row = db.query_one("SELECT * FROM motion_capture_records WHERE id = ?", (rid,))
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    if user["role"] not in TEACHER_ROLES and row["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权修改该记录")

    frames = db.json_load(row.get("frames"), {})
    ts = frames.get("ts") or []
    steps = db.json_load(row.get("steps"))
    segments = db.json_load(row.get("segments"))
    by_order = {s["order"]: s for s in segments}
    changes = {int(c["order"]): c for c in payload.get("segments") or []}
    if not ts:
        raise HTTPException(status_code=400, detail="该记录无帧数据，无法调整边界")

    for order, ch in changes.items():
        seg = by_order.get(order)
        if not seg or seg["result"] != "matched":
            continue
        start = _nearest_index(ts, float(ch["start_ts"]))
        end = _nearest_index(ts, float(ch["end_ts"]))
        start, end = min(start, end), max(start, end)
        end = min(end, len(ts) - 1)
        seg["start_idx"], seg["end_idx"] = start, end
        seg["start_ts"] = round(ts[start], 2)
        seg["end_ts"] = round(ts[end], 2)

    actions = _actions_map_for(_load_actions())
    events = sorted(
        ({"action_id": s["action_id"], "start_idx": s["start_idx"],
          "end_idx": s["end_idx"]}
         for s in segments if s["start_idx"] is not None),
        key=lambda e: (e["start_idx"], e["end_idx"]),
    )
    _, new_segments, score = build_assessment(steps, actions, events, frames)
    db.execute("UPDATE motion_capture_records SET segments=?, score=? WHERE id=?",
               (db.json_dump(new_segments), score, rid))
    out = db.query_one("SELECT * FROM motion_capture_records WHERE id = ?", (rid,))
    out["steps"] = db.json_load(out.get("steps"))
    out["segments"] = db.json_load(out.get("segments"))
    out.pop("frames", None)
    return out
```

- [ ] **Step 2: main.py 注册路由**

把 `from backend.routers import (auth_router, users, processes, actions, devices, monitoring, training, privacy)` 改为：

```python
from backend.routers import (auth_router, users, processes, actions,
                             devices, monitoring, training, privacy, assessment)
```

并在 `app.include_router(training.router)` 后加一行：

```python
app.include_router(assessment.router)
```

- [ ] **Step 3: 服务可启动冒烟**

启动服务（需 MySQL 密码环境变量与 P0 相同的 PYTHONPATH），确认 `/api/health` 200，再调用：

```powershell
Invoke-RestMethod http://localhost:8000/api/assessment/records -Headers $h
```

Expected: 返回 `{"items": []}`

- [ ] **Step 4: Commit**

```bash
git add backend/routers/assessment.py backend/main.py
git commit -m "feat(P1): assessment 会话与报告接口"
```
（用户在自己的终端执行）

### Task 4: 前端「动作对比」页 + 报告页

**Files:**
- Modify: `frontend/js/app.js`

**Interfaces:**
- Consumes: Task 3 全部接口
- Produces: 导航项 `assessment`（动作对比）；报告渲染 `App.renderAssessmentReport(rid)`

- [ ] **Step 1: 导航与视图注册**

`navItems()` 的 base 数组中、`{ key: "monitoring"...}` 之后加：

```js
      { key: "assessment", ico: "⚖️", label: "动作对比" },
```

`go()` 的 titles 中加：

```js
      assessment: "动作对比",
```

`go()` 的 views 中加：

```js
      assessment: () => this.renderAssessment(),
```

`go()` 中离开实时监控的清理条件改为：

```js
    if ((view !== "monitoring" && view !== "assessment") && this.state.monSession) this.stopMonitor(true);
```

- [ ] **Step 2: 会话页渲染（追加到 App 对象，monitoring 相关函数之后）**

```js
  // ---------- 动作对比 ----------
  async renderAssessment() {
    const c = document.getElementById("content");
    const [procs, data] = await Promise.all([
      this.api("/api/processes"), this.api("/api/assessment/records")]);
    const pname = {};
    procs.items.forEach(p => pname[p.id] = p.name);
    const procOpts = procs.items.map(p => `<option value="${p.id}">${this.esc(p.name)}</option>`).join("");
    c.innerHTML = `
      <div class="card">
        <div class="card-head"><h3>动作对比（摄像头采集 → 自动评分）</h3>
          <span id="as-status" class="tag tag-gray">未开始</span></div>
        <div class="grid" style="grid-template-columns:1fr 1fr auto;gap:12px;align-items:end">
          <div class="form-row" style="margin:0"><label>护理流程</label>
            <select id="as-process">${procOpts}</select></div>
          <div class="form-row" style="margin:0"><label>自定义视频源（留空用本机摄像头）</label>
            <input id="as-source" placeholder="rtsp://... 或留空"></div>
          <div style="display:flex;gap:8px">
            <button class="btn btn-primary" id="as-start" onclick="App.startAssessment()">▶ 开始</button>
            <button class="btn btn-danger" id="as-stop" onclick="App.stopAssessment()" disabled>结束</button>
          </div>
        </div>
        <div class="grid" style="grid-template-columns:1.5fr 1fr;margin-top:16px">
          <div style="background:#0f1115;border-radius:10px;min-height:340px;display:flex;align-items:center;justify-content:center;overflow:hidden">
            <img id="as-stream" style="max-width:100%;max-height:520px;display:none">
            <span id="as-placeholder" style="color:#4b5563">选流程后点「开始」，完整做一遍标准流程</span>
          </div>
          <div>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
              <b>步骤状态</b><span id="as-score" class="tag tag-gray">完成度 --</span>
            </div>
            <div id="as-steps" class="step-list"><div class="empty">开始后实时显示</div></div>
          </div>
        </div>
      </div>
      <div class="card"><div class="card-head"><h3>对比记录</h3></div>
      <table><thead><tr><th>流程</th><th>得分</th><th>遗漏</th><th>顺序错误</th><th>时间</th><th>操作</th></tr></thead>
      <tbody>${data.items.length ? data.items.map(r => `<tr>
        <td>${this.esc(pname[r.process_id] || "-")}</td>
        <td>${r.score == null ? '<span class="tag tag-gray">无标准</span>' : `<span class="tag ${r.score >= 80 ? "tag-green" : r.score >= 60 ? "tag-amber" : "tag-red"}">${r.score}分</span>`}</td>
        <td>${r.missed ? `<span class="tag tag-red">${r.missed} 步</span>` : '<span class="tag tag-green">无</span>'}</td>
        <td>${r.order_errors ? `<span class="tag tag-amber">${r.order_errors} 步</span>` : '<span class="tag tag-green">无</span>'}</td>
        <td>${new Date((r.started_at || 0) * 1000).toLocaleString()}</td>
        <td><button class="btn btn-sm btn-primary" onclick="App.renderAssessmentReport('${r.id}')">查看报告</button></td>
      </tr>`).join("") : `<tr><td colspan="6">${this.emptyHtml("暂无对比记录")}</td></tr>`}</tbody></table></div>`;
  },
  async startAssessment() {
    const processId = document.getElementById("as-process").value;
    const source = document.getElementById("as-source").value.trim() || "0";
    if (!processId) { alert("请选择流程"); return; }
    try {
      const data = await this.api("/api/assessment/sessions", "POST", { source, process_id: processId });
      this.state.monSession = data.session_id;
      this.state.assessMode = true;
      const img = document.getElementById("as-stream");
      img.style.display = "block";
      document.getElementById("as-placeholder").style.display = "none";
      document.getElementById("as-start").disabled = true;
      document.getElementById("as-stop").disabled = false;
      document.getElementById("as-status").className = "tag tag-green";
      document.getElementById("as-status").textContent = "采集中";
      this.refreshFrame();
      if (this._asFrameTimer) clearInterval(this._asFrameTimer);
      this._asFrameTimer = setInterval(() => this.refreshFrame(), 120);
      this.pollAssessment();
    } catch (e) { alert(e.message); }
  },
  async stopAssessment(recordId) {
    if (this.state.monSession && !recordId) {
      try {
        const r = await this.api("/api/assessment/sessions/" + this.state.monSession, "DELETE");
        recordId = r.record_id;
      } catch (e) {}
    }
    this.state.monSession = null;
    this.state.assessMode = false;
    if (this._asFrameTimer) { clearInterval(this._asFrameTimer); this._asFrameTimer = null; }
    if (this._asTimer) { clearInterval(this._asTimer); this._asTimer = null; }
    const img = document.getElementById("as-stream");
    if (img) { img.style.display = "none"; img.src = ""; }
    const ph = document.getElementById("as-placeholder");
    if (ph) ph.style.display = "";
    const st = document.getElementById("as-start");
    if (st) st.disabled = false;
    const sp = document.getElementById("as-stop");
    if (sp) sp.disabled = true;
    const ms = document.getElementById("as-status");
    if (ms) { ms.className = "tag tag-gray"; ms.textContent = "未开始"; }
    if (recordId) this.renderAssessmentReport(recordId);
  },
  pollAssessment() {
    if (this._asTimer) clearInterval(this._asTimer);
    this._asTimer = setInterval(async () => {
      const sid = this.state.monSession;
      if (!sid) { clearInterval(this._asTimer); this._asTimer = null; return; }
      try {
        const s = await this.api("/api/monitoring/sessions/" + sid + "/state");
        this.renderAssessmentState(s);
        if (s.running === false) {
          this.stopAssessment();
        }
      } catch (e) {}
    }, 600);
  },
  renderAssessmentState(s) {
    const scoreEl = document.getElementById("as-score");
    if (scoreEl) {
      scoreEl.textContent = "完成度 " + (s.score != null ? s.score + "%" : "--");
      scoreEl.className = "tag " + (s.score >= 80 ? "tag-green" : s.score >= 60 ? "tag-amber" : "tag-red");
    }
    const stepsEl = document.getElementById("as-steps");
    if (stepsEl && s.steps) {
      stepsEl.innerHTML = s.steps.map(st => {
        const cls = st.status === "done" ? "done" : st.status === "miss" ? "miss"
          : st.status === "current" ? "current" : "";
        const label = st.status === "done" ? "✓已完成" : st.status === "miss" ? "⚠漏步"
          : st.status === "current" ? "▶当前" : "待执行";
        return `<div class="step-item ${cls}"><span class="order">${st.order}</span>
          <span>${this.esc(st.name)}</span><span style="margin-left:auto;font-size:12px;color:var(--muted)">${label}</span></div>`;
      }).join("");
    }
  },
```

- [ ] **Step 3: 报告页渲染**

```js
  async renderAssessmentReport(rid) {
    const c = document.getElementById("content");
    let r;
    try { r = await this.api("/api/assessment/records/" + rid); }
    catch (e) { alert(e.message); return; }
    const segRows = (r.segments || []).map(s => {
      const tagMap = { matched: '<span class="tag tag-green">完成</span>',
        order_error: '<span class="tag tag-amber">顺序错误</span>',
        missed: '<span class="tag tag-red">遗漏</span>' };
      const scoreHtml = s.comparable
        ? (s.score == null ? '<span class="tag tag-gray">--</span>' : `${s.score} 分`)
        : '<span class="tag tag-gray">无标准</span>';
      const startSec = s.start_ts != null ? s.start_ts.toFixed(2) : "";
      const endSec = s.end_ts != null ? s.end_ts.toFixed(2) : "";
      const editable = s.result === "matched" ? `
        <input type="number" step="0.1" data-order="${s.order}" data-edge="start" value="${startSec}" style="width:76px">
        ~ <input type="number" step="0.1" data-order="${s.order}" data-edge="end" value="${endSec}" style="width:76px"> 秒` : startSec ? `${startSec} ~ ${endSec} 秒` : "--";
      return `<tr><td>${s.order}. ${this.esc(s.name)}</td><td>${tagMap[s.result] || s.result}</td>
        <td>${editable}</td><td>${scoreHtml}</td></tr>`;
    }).join("");
    c.innerHTML = `
      <div class="card">
        <div class="card-head"><h3>对比报告 · ${this.esc(r.process_name || "")}</h3>
          <button class="btn" onclick="App.go('assessment')">返回</button></div>
        <div class="grid grid-4" style="margin-bottom:16px">
          <div class="stat"><div class="num">${r.score == null ? "--" : r.score}</div><div class="lbl">总分（有标准步骤平均）</div></div>
          <div class="stat"><div class="num">${(r.segments || []).filter(s => s.result === "missed").length}</div><div class="lbl">遗漏步数</div></div>
          <div class="stat"><div class="num">${(r.segments || []).filter(s => s.result === "order_error").length}</div><div class="lbl">顺序错误步数</div></div>
          <div class="stat"><div class="num">${(r.segments || []).filter(s => s.comparable).length}</div><div class="lbl">可评分步数</div></div>
        </div>
        <div class="banner banner-info">完成步可在「边界」列直接改起止秒数后点保存重算。</div>
        <table><thead><tr><th>步骤</th><th>结果</th><th>边界</th><th>得分</th></tr></thead>
        <tbody>${segRows || `<tr><td colspan="4">${this.emptyHtml("无步骤")}</td></tr>`}</tbody></table>
        <div class="modal-actions"><button class="btn btn-primary" onclick="App.saveAssessmentSegments('${rid}')">保存边界并重算</button></div>
      </div>`;
  },
  async saveAssessmentSegments(rid) {
    const rows = [...document.querySelectorAll("#content input[data-edge]")];
    const segments = rows.reduce((acc, el) => {
      const o = Number(el.dataset.order);
      acc[o] = acc[o] || { order: o };
      acc[o][el.dataset.edge === "start" ? "start_ts" : "end_ts"] = parseFloat(el.value) || 0;
      return acc;
    }, {});
    try {
      const r = await this.api("/api/assessment/records/" + rid + "/segments", "PUT",
        { segments: Object.values(segments) });
      alert("已重算，总分 " + (r.score == null ? "--" : r.score));
      this.renderAssessmentReport(rid);
    } catch (e) { alert(e.message); }
  },
```

- [ ] **Step 4: 页面检查**

服务启动后浏览器打开 `http://localhost:8000`，登录后点「动作对比」：Expected 页面正常渲染、F12 Console 无 JS 报错。

- [ ] **Step 5: Commit**

```bash
git add frontend/js/app.js
git commit -m "feat(P1): 动作对比会话页与报告页"
```
（用户在自己的终端执行）

### Task 5: 集成验收

**Files:** 无（只验证）

- [ ] **Step 1: 纯逻辑全量自检**

Run: `python -m tests.test_database_compat` 与 `python -m tests.test_assessment`
Expected: 两个均输出 OK。

- [ ] **Step 2: API 冒烟**

服务启动后：
1. `POST /api/assessment/sessions` 传不存在的 `process_id` → 400「请选择有效的护理流程」。
2. 传存在的 `process_id` + `source=0` 启动会话 → 返回 session_id（若本机无摄像头，会话状态会报错，属预期；采集验收留待有摄像头机器）。
3. `GET /api/assessment/records` 返回 items（空或含记录）。

- [ ] **Step 3: 用户手动验收（有摄像头时）**

```powershell
python record_template.py --name "双手靠近序列" --duration 3
```
然后网页端：动作对比 → 选含该动作的流程 → 完整做一遍 → 报告显示每步得分/遗漏/顺序错误；改边界保存后分数变化。

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(P1): 对比引擎完成"
```
（用户在自己的终端执行）
