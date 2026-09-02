"""隐私安全设置接口"""
from fastapi import APIRouter, Depends

from backend import auth
import config
from backend import database as db
from .auth_router import current_user

router = APIRouter(prefix="/api/privacy", tags=["privacy"])


def _ensure_defaults():
    for k, v in config.PRIVACY_DEFAULTS.items():
        if not db.query_one("SELECT key FROM privacy_settings WHERE key=?", (k,)):
            db.execute("INSERT INTO privacy_settings (key, value, updated_at) VALUES (?,?,?)",
                       (k, "true" if v else "false", db.now()))


@router.get("/settings")
def get_settings(user=Depends(current_user)):
    _ensure_defaults()
    rows = db.query("SELECT key, value, updated_at FROM privacy_settings")
    out = {}
    for r in rows:
        out[r["key"]] = r["value"] in ("true", "1", "True")
    return {"settings": out}


@router.put("/settings")
def update_settings(payload: dict, user=Depends(current_user)):
    auth.require_role(user, ["admin"])
    settings = payload.get("settings") or {}
    for k, v in settings.items():
        if k in config.PRIVACY_DEFAULTS:
            db.execute(
                "INSERT INTO privacy_settings (key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (k, "true" if v else "false", db.now()),
            )
    auth.audit(user, "update_privacy", "更新隐私安全设置")
    return {"ok": True}


@router.get("/audit-logs")
def audit_logs(user=Depends(current_user)):
    auth.require_role(user, ["admin"])
    rows = db.query("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 200")
    return {"items": rows}
