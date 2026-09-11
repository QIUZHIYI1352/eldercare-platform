"""任务接口：教师发布/管理任务，学员查看我的任务"""
from fastapi import APIRouter, Depends, HTTPException

from backend import auth
from backend import database as db
from .auth_router import current_user

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
