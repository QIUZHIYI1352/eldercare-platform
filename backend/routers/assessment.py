"""动作对比：采集会话 + 报告（总分、每步得分、遗漏/顺序错误）"""
import bisect

from fastapi import APIRouter, Depends, HTTPException

from backend import auth
from backend import database as db
from backend.vision.assessment import build_assessment
from backend.vision.monitor_session import manager
from .auth_router import current_user
from .tasks import STUDENT_ROLES

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
    row = db.query_one(
        "SELECT c.*, p.name AS process_name FROM motion_capture_records c "
        "LEFT JOIN processes p ON c.process_id = p.id WHERE c.id = ?", (rid,))
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
    out = db.query_one(
        "SELECT c.*, p.name AS process_name FROM motion_capture_records c "
        "LEFT JOIN processes p ON c.process_id = p.id WHERE c.id = ?", (rid,))
    out["steps"] = db.json_load(out.get("steps"))
    out["segments"] = db.json_load(out.get("segments"))
    out.pop("frames", None)
    return out
