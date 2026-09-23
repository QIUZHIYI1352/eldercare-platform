"""护理流程（培训标准）接口"""
from fastapi import APIRouter, Depends, HTTPException

from backend import auth
from backend import database as db
from .auth_router import current_user
from .tasks import TEACHER_ROLES

router = APIRouter(prefix="/api/processes", tags=["processes"])


def _require_manager(user):
    """护理流程是培训标准，只有教师/管理员可维护；所有登录用户可读取。"""
    if user["role"] not in TEACHER_ROLES:
        raise HTTPException(status_code=403, detail="仅教师或管理员可维护培训标准流程")


@router.get("")
def list_processes(user=Depends(current_user)):
    rows = db.query("SELECT * FROM processes ORDER BY created_at DESC")
    for r in rows:
        r["steps"] = db.json_load(r.get("steps"))
        r["steps_count"] = len(r["steps"])
    return {"items": rows}


@router.get("/{pid}")
def get_process(pid: str, user=Depends(current_user)):
    row = db.query_one("SELECT * FROM processes WHERE id = ?", (pid,))
    if not row:
        raise HTTPException(status_code=404, detail="流程不存在")
    row["steps"] = db.json_load(row.get("steps"))
    return row


@router.post("")
def create_process(payload: dict, user=Depends(current_user)):
    _require_manager(user)
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="流程名称必填")
    pid = db.gen_id("proc")
    db.execute(
        "INSERT INTO processes (id, name, category, description, steps, created_by, created_at) VALUES (?,?,?,?,?,?,?)",
        (pid, name, payload.get("category") or "基础护理", payload.get("description") or "",
         db.json_dump(payload.get("steps") or []), user["id"], db.now()),
    )
    auth.audit(user, "create_process", f"创建护理流程 {name}")
    return {"id": pid}


@router.put("/{pid}")
def update_process(pid: str, payload: dict, user=Depends(current_user)):
    _require_manager(user)
    row = db.query_one("SELECT * FROM processes WHERE id = ?", (pid,))
    if not row:
        raise HTTPException(status_code=404, detail="流程不存在")
    db.execute(
        "UPDATE processes SET name=?, category=?, description=?, steps=? WHERE id=?",
        (payload.get("name") or row["name"], payload.get("category") or row["category"],
         payload.get("description") or row["description"],
         db.json_dump(payload.get("steps", db.json_load(row["steps"]))), pid),
    )
    auth.audit(user, "update_process", f"更新护理流程 {row['name']}")
    return {"ok": True}


@router.delete("/{pid}")
def delete_process(pid: str, user=Depends(current_user)):
    _require_manager(user)
    db.execute("DELETE FROM processes WHERE id = ?", (pid,))
    auth.audit(user, "delete_process", f"删除护理流程 {pid}")
    return {"ok": True}
