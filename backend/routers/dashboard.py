"""教师端：实时预警看板 + 学员档案聚合"""
from fastapi import APIRouter, Depends, HTTPException

from backend import auth
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
    is_admin = user["role"] == "admin"
    sessions = []
    for sid in manager.list_sessions():
        sess = manager.get(sid)
        if sess is None or not sess.assess or sess._running is False:
            continue
        st = sess.get_state()
        class_name = task_name = ""
        if sess.task_id:
            t = db.query_one(
                "SELECT t.title, c.name AS class_name, c.owner_user_id AS owner "
                "FROM tasks t JOIN classes c ON t.class_id = c.id WHERE t.id = ?",
                (sess.task_id,))
            if not t:
                continue
            if not is_admin and t["owner"] != user["id"]:
                continue
            task_name, class_name = t["title"], t["class_name"]
        elif not is_admin:
            continue  # 自由练习仅 admin 看板可见
        u = db.query_one("SELECT name FROM users WHERE id = ?", (sess.user_id,))
        sessions.append({
            "session_id": sid,
            "user_id": sess.user_id,
            "user_name": (u or {}).get("name", "") if sess.user_id else "自由练习",
            "task_name": task_name, "class_name": class_name,
            "process_name": sess.process.get("name", ""),
            "score": st.get("score"), "current_step": st.get("current_step"),
            "step_stay": st.get("step_stay", 0),
            "off_screen": st.get("off_screen", 0),
            "alert_level": st.get("alert_level", "ok"),
        })

    where = "WHERE c.task_id <> ''"
    params = ()
    if not is_admin:
        where += " AND cl.owner_user_id = ?"
        params = (user["id"],)
    rows = db.query(
        "SELECT c.id, c.user_id, c.score, c.segments, c.started_at, u.name AS user_name, "
        "t.title AS task_name, cl.name AS class_name "
        "FROM motion_capture_records c "
        "LEFT JOIN users u ON c.user_id = u.id "
        "LEFT JOIN tasks t ON c.task_id = t.id "
        "LEFT JOIN classes cl ON t.class_id = cl.id " + where +
        " ORDER BY c.started_at DESC LIMIT 50",
        params,
    )
    alerts = []
    for r in rows:
        missed, order_err = _segment_counts(r)
        if (r["score"] is not None and r["score"] < 60) or missed or order_err:
            alerts.append({
                "record_id": r["id"], "user_id": r["user_id"],
                "user_name": r["user_name"] or "",
                "class_name": r["class_name"] or "", "task_name": r["task_name"] or "",
                "score": r["score"], "missed": missed, "order_errors": order_err,
                "started_at": r["started_at"],
            })
    return {"sessions": sessions, "alerts": alerts[:30],
            "thresholds": manager_thresholds()}


THRESHOLD_KEYS = {"step_warn": "alert_step_warn",
                  "step_crit": "alert_step_crit",
                  "off": "alert_off_seconds"}
DEFAULT_THRESHOLDS = {"step_warn": 30, "step_crit": 60, "off": 5}


def manager_thresholds():
    conf = {r["key"]: r["value"] for r in
            db.query("SELECT `key`, value FROM privacy_settings")}
    out = {}
    for name, key in THRESHOLD_KEYS.items():
        try:
            out[name] = int(conf.get(key, DEFAULT_THRESHOLDS[name]))
        except (TypeError, ValueError):
            out[name] = DEFAULT_THRESHOLDS[name]
    return out


@router.get("/thresholds")
def get_thresholds(user=Depends(current_user)):
    _teacher_only(user)
    return manager_thresholds()


@router.put("/thresholds")
def update_thresholds(payload: dict, user=Depends(current_user)):
    _teacher_only(user)
    for name, key in THRESHOLD_KEYS.items():
        if name in payload:
            db.execute(
                "INSERT INTO privacy_settings (`key`, value, updated_at) VALUES (?,?,?) "
                "ON DUPLICATE KEY UPDATE value=VALUES(value), updated_at=VALUES(updated_at)",
                (key, str(int(payload[name])), db.now()),
            )
    auth.audit(user, "update_alert_thresholds", f"更新预警阈值 {payload}")
    return manager_thresholds()


def _visible_class(cid, user):
    cls = db.query_one("SELECT * FROM classes WHERE id = ?", (cid,))
    if not cls or not can_manage(user, cls["owner_user_id"]):
        raise HTTPException(status_code=403, detail="无权限")
    return cls


_SORTS = {"name": "u.name", "avg_score": "avg_score", "last_at": "last_at",
          "warnings": "warnings"}


@teacher_router.get("/students")
def students(class_id: str, q: str = "", sort: str = "name",
             user=Depends(current_user)):
    _teacher_only(user)
    _visible_class(class_id, user)
    order_col = _SORTS.get(sort, "u.name")
    direction = "ASC" if sort == "name" else "DESC"
    where = "WHERE m.class_id = ?"
    params = [class_id]
    if q.strip():
        where += " AND (u.name LIKE ? OR u.username LIKE ?)"
        params += ["%" + q.strip() + "%", "%" + q.strip() + "%"]
    rows = db.query(
        "SELECT u.id, u.username, u.name, u.organization, "
        "(SELECT COUNT(*) FROM training_records tr WHERE tr.user_id = u.id) AS records_count, "
        "(SELECT COUNT(DISTINCT tr.task_id) FROM training_records tr "
        " WHERE tr.user_id = u.id AND tr.task_id <> '') AS tasks_done, "
        "(SELECT ROUND(AVG(tr.score), 1) FROM training_records tr "
        " WHERE tr.user_id = u.id) AS avg_score, "
        "(SELECT MAX(tr.created_at) FROM training_records tr "
        " WHERE tr.user_id = u.id) AS last_at, "
        "(SELECT COUNT(*) FROM motion_capture_records mc WHERE mc.user_id = u.id "
        " AND mc.task_id <> '' AND (mc.score < 60 "
        " OR mc.segments LIKE '%order_error%' "
        " OR mc.segments LIKE '%\"result\": \"missed\"%')) AS warnings "
        "FROM class_members m JOIN users u ON m.user_id = u.id "
        + where + " ORDER BY " + order_col + " " + direction,
        tuple(params),
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
