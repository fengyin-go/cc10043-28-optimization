"""业务路由汇总：固定业务路由在前，通用模块资源路由兜底。"""
from __future__ import annotations

from app.routers import offset_commits, resource

# offset_commits 携带 /api/offset-commits 固定前缀，必须排在 /api/{module} 之前，
# 否则会被通用资源路由当作模块名匹配。
ROUTERS = [offset_commits, resource]
