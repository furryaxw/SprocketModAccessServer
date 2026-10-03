"""部署路径。

服务可以从任意工作目录启动，因此 `.env`、`data/`、`logs/` 与前端构建产物一律按检出位置解析，
不按当前目录解析。
"""

from __future__ import annotations

import os
from pathlib import Path

# 本文件位于 <repo>/src/sprocket_access_server/infrastructure/configuration/paths.py
_PACKAGE_DIR = Path(__file__).resolve().parents[2]
_SOURCE_DIR = _PACKAGE_DIR.parent
REPOSITORY_ROOT = _SOURCE_DIR.parent


def environment_file() -> Path:
    """`.env` 的位置：检出根，与工作目录无关。"""
    return REPOSITORY_ROOT / ".env"


def installation_root() -> Path:
    """运行状态（数据库、包、日志）的根；`SMAS_ROOT` 可把它放到检出之外。"""
    configured = os.environ.get("SMAS_ROOT", "").strip()
    if not configured:
        return REPOSITORY_ROOT
    path = Path(configured).expanduser()
    # 相对值按检出根解析：按当前目录解析就等于 CWD 依赖换个地方冒出来。
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def admin_ui_dist() -> Path:
    """管理端构建产物；由检出位置决定。"""
    return REPOSITORY_ROOT / "admin-ui" / "dist"
