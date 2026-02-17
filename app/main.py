from __future__ import annotations

from typing import Callable
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import router as v1_router
from app.common.errors import AppError, INVALID_PARAMS, app_error_from_http, envelope
from app.common.logging import init_logging
from app.common.config import settings
from app.infra.db import init_db
from app.agent.tools_registry import list_tools

logger = init_logging()

app = FastAPI(title="smart-eats")


@app.on_event("startup")
async def on_startup() -> None:
    from app.agent.llm_adapters import ProviderRegistry
    config = ProviderRegistry.get(settings.LLM_PROVIDER)
    logger.info(
        "llm provider=%s model_planner=%s model_writer=%s key_set=%s",
        config.name,
        config.model_planner,
        config.model_writer,
        bool(config.api_key),
    )
    tools = list_tools()
    logger.info("tools_registered count=%s names=%s", len(tools), [t["name"] for t in tools])
    await init_db()
    
    # Preload RAG embedding model to avoid cold start latency
    try:
        from app.agent.rag import base as rag
        rag.warmup()
    except Exception as e:
        logger.warning("RAG warmup skipped: %s", e)


@app.middleware("http")
async def trace_middleware(request: Request, call_next: Callable):
    trace_id = str(uuid4())
    request.state.trace_id = trace_id
    logger.info("request %s %s trace_id=%s", request.method, request.url.path, trace_id)
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    trace_id = getattr(request.state, "trace_id", "")
    payload = envelope(None, trace_id, code=exc.code, message=exc.message)
    return JSONResponse(status_code=exc.http_status, content=payload)


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    app_exc = app_error_from_http(exc)
    trace_id = getattr(request.state, "trace_id", "")
    payload = envelope(None, trace_id, code=app_exc.code, message=app_exc.message)
    return JSONResponse(status_code=app_exc.http_status, content=payload)


app.include_router(v1_router)

app.mount("/", StaticFiles(directory="app/static", html=True), name="static")


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    trace_id = getattr(request.state, "trace_id", "")
    payload = envelope(None, trace_id, code=INVALID_PARAMS, message="invalid params")
    return JSONResponse(status_code=422, content=payload)
