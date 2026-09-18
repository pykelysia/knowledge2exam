"""FastAPI 应用入口。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import update

from app.api import artifacts, auth, catalog, jobs, revisions, uploads
from app.config import DEFAULT_JWT_SECRET, settings
from app.core.db import AsyncSessionLocal
from app.core.exceptions import AppException, ErrorCode
from app.models.job import Job
from app.orchestration.state_machine import TERMINAL_STATUSES
from app.rendering.setup import ensure_pandoc_available
from app.schemas.common import ErrorResponse


async def _recover_interrupted_jobs() -> int:
    """服务重启后把非终态任务定格为 failed（进程内任务已随重启丢失）。"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(Job)
            .where(Job.status.not_in([s.value for s in TERMINAL_STATUSES]))
            .values(
                status="failed",
                error_code=ErrorCode.SERVER_RESTARTED.value,
                finished_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    return result.rowcount


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """启动校验与恢复：JWT 密钥防护、中断任务收敛、渲染依赖检查。"""
    if not settings.debug_mode and settings.jwt_secret == DEFAULT_JWT_SECRET:
        raise RuntimeError(
            "生产环境（DEBUG_MODE=false）必须配置 JWT_SECRET，拒绝使用内置开发密钥启动"
        )

    recovered = await _recover_interrupted_jobs()
    if recovered:
        logging.getLogger(__name__).warning(
            "服务重启：已将 %d 个非终态任务定格为 failed（SERVER_RESTARTED）", recovered
        )

    # 渲染依赖缺失时启动报错（渲染期会自动降级 md_only）；安装由部署显式负责
    ensure_pandoc_available(auto_install=True, raise_on_missing=True)
    yield


app = FastAPI(
    title="knowledge2exam API",
    version="1.0.0",
    description="试卷生成工具后端接口（REST + SSE）。",
    lifespan=lifespan,
)


def _setup_debug_logger() -> None:
    """配置 debug logger。

    - `log_step` 的输出仍受 ``settings.debug_mode`` 控制（在 debug_log.py 内判断）。
    - file handler 始终注册，确保 ``log_error`` 的错误信息无论 debug_mode 如何都能落盘。
    """
    debug_logger = logging.getLogger("k2e.debug")
    debug_logger.setLevel(logging.DEBUG)  # 允许所有级别通过，过滤在调用方

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    log_file = log_dir / f"debug_{datetime.now(UTC):%Y%m%d}.log"
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    debug_logger.addHandler(handler)


_setup_debug_logger()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api/v1"

app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(uploads.router, prefix=API_PREFIX)
app.include_router(jobs.router, prefix=API_PREFIX)
app.include_router(artifacts.router, prefix=API_PREFIX)
app.include_router(catalog.router, prefix=API_PREFIX)
app.include_router(revisions.router, prefix=API_PREFIX)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error_code=exc.code, message=exc.message, detail=exc.detail
        ).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # FastAPI 参数/载荷校验失败统一映射为 400，符合契约的错误码取 INPUT_EMPTY
    # 作为兜底（更精确的码由业务层自行抛出）。
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(
            error_code=ErrorCode.INPUT_EMPTY,
            message="请求参数或载荷不合法",
            detail={"errors": exc.errors()},
        ).model_dump(),
    )


@app.get("/health", tags=["Meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
