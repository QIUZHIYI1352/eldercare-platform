"""监控记录 + 实时动作识别状态接口"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from backend import auth
from backend import database as db
from backend.vision.monitor_session import manager
from .auth_router import current_user

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])


@router.get("/records")
def list_records(user=Depends(current_user)):
    rows = db.query("SELECT * FROM monitoring_records ORDER BY started_at DESC LIMIT 200")
    for r in rows:
        r["detected_actions"] = db.json_load(r.get("detected_actions"))
        r["missed_steps"] = db.json_load(r.get("missed_steps"))
        r["completed_steps"] = db.json_load(r.get("completed_steps"))
    return {"items": rows}


@router.post("/records")
def create_record(payload: dict, user=Depends(current_user)):
    rid = db.gen_id("mon")
    db.execute(
        "INSERT INTO monitoring_records (id, device_id, process_id, user_id, started_at, status) VALUES (?,?,?,?,?,?)",
        (rid, payload.get("device_id") or "", payload.get("process_id") or "",
         user["id"], db.now(), "running"),
    )
    return {"id": rid}


@router.post("/records/{rid}/finish")
def finish_record(rid: str, payload: dict, user=Depends(current_user)):
    db.execute(
        "UPDATE monitoring_records SET ended_at=?, detected_actions=?, missed_steps=?, completed_steps=?, status=? WHERE id=?",
        (db.now(), db.json_dump(payload.get("detected_actions") or []),
         db.json_dump(payload.get("missed_steps") or []),
         db.json_dump(payload.get("completed_steps") or []),
         "finished", rid),
    )
    return {"ok": True}


@router.get("/live/state")
def live_state(user=Depends(current_user)):
    """返回当前实时识别所需的静态配置（动作模板、流程），供前端/引擎使用。"""
    actions = db.query("SELECT * FROM actions ORDER BY created_at DESC")
    for a in actions:
        a["conditions"] = db.json_load(a.get("conditions"))
    processes = db.query("SELECT id, name, steps FROM processes")
    for p in processes:
        p["steps"] = db.json_load(p.get("steps"))
    return {"actions": actions, "processes": processes}


# ---------- Web 内嵌实时监控会话 ----------

@router.post("/sessions")
def start_session(payload: dict, user=Depends(current_user)):
    """创建监控会话：{source, process_id} -> {session_id}"""
    source = (payload.get("source") or "0").strip()
    process_id = payload.get("process_id") or ""
    process = db.query_one("SELECT * FROM processes WHERE id = ?", (process_id,))
    if not process:
        raise HTTPException(status_code=400, detail="请选择有效的护理流程")
    process["steps"] = db.json_load(process.get("steps"))
    actions = db.query("SELECT * FROM actions ORDER BY created_at")
    for a in actions:
        a["conditions"] = db.json_load(a.get("conditions"))
        a["template_data"] = db.json_load(a.get("template_data"), {})
    try:
        sid = manager.start(source, process, actions, user["id"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"监控启动失败: {e}")
    auth.audit(user, "start_monitor", f"启动实时监控 流程={process['name']} 源={source}")
    return {"session_id": sid}


@router.get("/sessions")
def list_sessions(user=Depends(current_user)):
    return {"items": manager.list_sessions()}


# 可查看任意画面的角色（教师/管理员用于教学看板）
_VIEW_ALL_ROLES = {"admin", "elderly_service_teacher"}


def _authorized_session(sid: str, token: str = ""):
    """画面接口鉴权。

    <img> 标签无法携带 Authorization 头，因此这里接受 ?token= 查询参数；
    但必须校验通过，且只能查看自己发起的会话（教师/管理员可看全部）。
    """
    user = auth.get_user_by_token(token) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="未授权：请提供有效的会话令牌")
    sess = manager.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在或已结束")
    if user["role"] not in _VIEW_ALL_ROLES and sess.user_id != user["id"]:
        raise HTTPException(status_code=403, detail="无权查看该会话画面")
    return sess


@router.get("/sessions/{sid}/stream")
def session_stream(sid: str, token: str = ""):
    """MJPEG 视频流（保留，兼容 Firefox/Safari）。

    注意：Chrome/Edge 对 multipart/x-mixed-replace 支持不完整，
    前端默认改用 /frame 单帧接口，此接口作为备用。
    """
    _authorized_session(sid, token)
    return StreamingResponse(
        manager.frame_generator(sid),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@router.get("/sessions/{sid}/frame")
def session_frame(sid: str, token: str = ""):
    """返回最新一帧 JPEG（单帧轮询，兼容所有浏览器，避免 MJPEG 兼容问题）。"""
    from fastapi.responses import Response
    sess = _authorized_session(sid, token)
    jpeg, _ts = sess.get_jpeg()
    if jpeg is None:
        raise HTTPException(status_code=404, detail="暂无画面帧，请稍候")
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store, no-cache"})


@router.get("/sessions/{sid}/state")
def session_state(sid: str, user=Depends(current_user)):
    sess = manager.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在或已结束")
    return sess.get_state()


@router.delete("/sessions/{sid}")
def stop_session(sid: str, user=Depends(current_user)):
    manager.stop(sid)
    return {"ok": True}
