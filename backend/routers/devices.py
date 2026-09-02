"""监控设备管理接口"""
from fastapi import APIRouter, Depends, HTTPException

from backend import auth
from backend import database as db
from .auth_router import current_user

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("")
def list_devices(user=Depends(current_user)):
    rows = db.query("SELECT * FROM devices ORDER BY created_at DESC")
    return {"items": rows}


@router.post("")
def create_device(payload: dict, user=Depends(current_user)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="设备名称必填")
    did = db.gen_id("dev")
    db.execute(
        "INSERT INTO devices (id, name, type, url, location, status, privacy_mask, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (did, name, payload.get("type") or "USB摄像头", payload.get("url") or "0",
         payload.get("location") or "", "offline", 1 if payload.get("privacy_mask", True) else 0,
         db.now()),
    )
    auth.audit(user, "create_device", f"添加监控设备 {name}")
    return {"id": did}


@router.put("/{did}")
def update_device(did: str, payload: dict, user=Depends(current_user)):
    row = db.query_one("SELECT * FROM devices WHERE id = ?", (did,))
    if not row:
        raise HTTPException(status_code=404, detail="设备不存在")
    db.execute(
        "UPDATE devices SET name=?, type=?, url=?, location=?, privacy_mask=? WHERE id=?",
        (payload.get("name") or row["name"], payload.get("type") or row["type"],
         payload.get("url") or row["url"], payload.get("location") or row["location"],
         1 if payload.get("privacy_mask", row["privacy_mask"]) else 0, did),
    )
    auth.audit(user, "update_device", f"更新监控设备 {row['name']}")
    return {"ok": True}


@router.delete("/{did}")
def delete_device(did: str, user=Depends(current_user)):
    db.execute("DELETE FROM devices WHERE id = ?", (did,))
    auth.audit(user, "delete_device", f"删除监控设备 {did}")
    return {"ok": True}
