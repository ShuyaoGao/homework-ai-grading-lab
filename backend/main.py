"""FastAPI 应用入口：API 路由 + 托管前端静态文件（一条命令启动）。"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import ROOT
from .db import init_db
from .routers import assignments, auth, classes, demo, meta, submissions

FRONTEND = ROOT / "frontend"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

DESCRIPTION = """
教育评价 AI 系统后端。流程：拍照上传 → 图像预处理 → 手写识别 → 按量规结构化评分 → 教师复核 → 学生画像。

**AI 评分仅供参考，以教师复核结果为准。**

鉴权：先调用 `/api/auth/login` 获取 Bearer token（点右上角 Authorize 填用户名密码即可）。
"""


def create_app() -> FastAPI:
    app = FastAPI(title="教育评价 AI 系统", version=__version__, description=DESCRIPTION,
                  docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json")
    init_db()
    for r in (auth.router, classes.router, assignments.router, submissions.router, meta.router, demo.router):
        app.include_router(r)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # noqa: ANN001, ANN202
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        if request.url.path.startswith("/api/") and "Cache-Control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    if FRONTEND.exists():
        @app.get("/sw.js", include_in_schema=False)
        def sw() -> FileResponse:
            return FileResponse(FRONTEND / "sw.js", media_type="application/javascript",
                                headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})

        app.mount("/", StaticFiles(directory=Path(FRONTEND), html=True), name="frontend")
    return app


app = create_app()
