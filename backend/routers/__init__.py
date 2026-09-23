"""路由聚合（backend.main 从这里统一导入并挂载）"""
from . import (  # noqa: F401
    auth_router, users, processes, actions, devices, monitoring, training,
    privacy, assessment, classes, tasks, dashboard, messages,
)
