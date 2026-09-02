"""FastAPI 应用入口"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import config
from backend import database as db
from backend.routers import (auth_router, users, processes, actions,
                             devices, monitoring, training, privacy)

app = FastAPI(title="智慧养老 · 辅助监管与培训系统", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


@app.on_event("startup")
def startup():
    db.init_db()
    from backend import seed
    seed.seed()


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
