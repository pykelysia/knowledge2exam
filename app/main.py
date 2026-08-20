"""FastAPI 应用入口。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import auth, catalog, jobs, uploads
from app.config import settings
from app.core.exceptions import AppException, ErrorCode
from app.schemas.common import ErrorResponse

app = FastAPI(
    title="knowledge2exam API",
    version="1.0.0",
    description="试卷生成工具后端接口（REST + SSE）。",
)

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
app.include_router(catalog.router, prefix=API_PREFIX)


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
