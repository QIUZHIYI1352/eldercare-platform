"""培训记录接口"""
from fastapi import APIRouter, Depends

from backend import auth
from backend import database as db
from .auth_router import current_user

router = APIRouter(prefix="/api/training", tags=["training"])


@router.get("/records")
def list_records(user=Depends(current_user)):
    rows = db.query(
        """SELECT t.*, p.name AS process_name, u.name AS user_name
           FROM training_records t
           LEFT JOIN processes p ON t.process_id = p.id
           LEFT JOIN users u ON t.user_id = u.id
           ORDER BY t.created_at DESC LIMIT 200"""
    )
    for r in rows:
        r["completed_steps"] = db.json_load(r.get("completed_steps"))
        r["missed_steps"] = db.json_load(r.get("missed_steps"))
        r["user_name"] = auth.mask_info(r.get("user_name") or "")  # 隐私脱敏
    return {"items": rows}


@router.post("/records")
def create_record(payload: dict, user=Depends(current_user)):
    rid = db.gen_id("train")
    db.execute(
        "INSERT INTO training_records (id, user_id, process_id, score, completed_steps, missed_steps, duration, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (rid, user["id"], payload.get("process_id") or "",
         float(payload.get("score") or 0),
         db.json_dump(payload.get("completed_steps") or []),
         db.json_dump(payload.get("missed_steps") or []),
         int(payload.get("duration") or 0), db.now()),
    )
    auth.audit(user, "training_done", f"完成一次培训，得分 {payload.get('score')}")
    return {"id": rid}
