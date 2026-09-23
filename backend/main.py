"""FastAPI 应用入口"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import config
from backend import auth
from backend import database as db
from backend.routers import (auth_router, users, processes, actions,
                             devices, monitoring, training, privacy, assessment,
                             classes, tasks, dashboard, messages)


@asynccontextmanager
async def lifespan(app):
    """启动时建库建表并写入种子数据（替代已废弃的 on_event）。"""
    db.init_db()
    from backend import seed
    seed.seed()
    yield


app = FastAPI(title="智慧实训规范平台", version="1.0.0", lifespan=lifespan)

# 跨域：默认仅放开本地开发常用来源，可用环境变量 CORS_ORIGINS 覆盖
# （多个来源用逗号分隔；设为 * 表示不限制，仅建议内网/调试环境使用）
_origins_raw = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000",
)
_origins = [o.strip() for o in _origins_raw.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 业务路由
app.include_router(auth_router.router)
app.include_router(users.router)
app.include_router(processes.router)
app.include_router(actions.router)
app.include_router(devices.router)
app.include_router(monitoring.router)
app.include_router(training.router)
app.include_router(privacy.router)
app.include_router(assessment.router)
app.include_router(classes.router)
app.include_router(tasks.router)
app.include_router(dashboard.router)
app.include_router(dashboard.teacher_router)
app.include_router(messages.router)


@app.exception_handler(auth.PermissionDenied)
async def _permission_denied_handler(request, exc):
    """权限不足统一返回 403（require_role 抛出的是 PermissionDenied）。

    注意：必须显式处理，否则这类异常会变成 500。
    """
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=403, content={"detail": str(exc) or "无权限"})


@app.get("/api/health")
def health():
    import importlib.util
    vision_available = (
        importlib.util.find_spec("cv2") is not None
        and importlib.util.find_spec("mediapipe") is not None
    )
    return {"status": "ok", "app": "eldercare", "vision_available": vision_available}


# 前端静态资源
frontend_dir = os.path.join(config.BASE_DIR, "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
