from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.agent.graph import run_chat_stream
from app.api.deps import db_dep, get_current_user_id, minio_dep, redis_dep
from app.common.config import settings
from app.common.errors import envelope
from app.common.sse import sse_event
from app.domain.app.schemas import (
    BlindBoxDrawRequest,
    ChangePasswordRequest,
    ChatSessionStreamRequest,
    ChatSessionUpdateRequest,
    HomeChefRecipeGenerateRequest,
    IngredientCreateRequest,
    IngredientUpdateRequest,
    LoginRequest,
    LogoutRequest,
    MePreferencesUpdateRequest,
    RefreshRequest,
    RegisterRequest,
    ScanApplyRequest,
    UpdateMeRequest,
    WheelCurrentUpdateRequest,
    WheelSpinRequest,
)
from app.domain.app.service import AppBffService
from app.tasks import fridge_recognition

router = APIRouter()


@router.post("/auth/register")
async def register(payload: RegisterRequest, request: Request, db: db_dep, redis: redis_dep):
    data = await AppBffService.register(payload.model_dump(), db, redis)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.post("/auth/login")
async def login(payload: LoginRequest, request: Request, db: db_dep, redis: redis_dep):
    client_ip = request.client.host if request.client else "unknown"
    data = await AppBffService.login(payload.model_dump(), db, redis, client_ip)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.post("/auth/refresh")
async def refresh(payload: RefreshRequest, request: Request, redis: redis_dep):
    data = await AppBffService.refresh(payload.refresh_token, redis)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.post("/auth/logout")
async def logout(
    payload: LogoutRequest,
    request: Request,
    redis: redis_dep,
    _user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.logout(payload.refresh_token, redis)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.post("/auth/password/change")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.change_password(
        user_id=user_id,
        old_password=payload.old_password,
        new_password=payload.new_password,
        db=db,
    )
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.get("/me")
async def get_me(request: Request, db: db_dep, user_id: str = Depends(get_current_user_id)):
    data = await AppBffService.get_me(user_id, db)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.patch("/me")
async def update_me(
    payload: UpdateMeRequest,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.update_me(user_id, payload.model_dump(exclude_unset=True), db)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.get("/me/preferences")
async def get_preferences(
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.get_preferences(user_id, db)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.patch("/me/preferences")
async def update_preferences(
    payload: MePreferencesUpdateRequest,
    request: Request,
    db: db_dep,
    redis: redis_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.update_preferences(
        user_id=user_id,
        payload=payload.model_dump(exclude_unset=True),
        db=db,
        redis_client=redis,
    )
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.get("/fridge/ingredients")
async def list_ingredients(
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.list_ingredients(user_id, db)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.post("/fridge/ingredients")
async def create_ingredient(
    payload: IngredientCreateRequest,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.create_ingredient(user_id, payload.model_dump(), db)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.patch("/fridge/ingredients/{ingredient_id}")
async def update_ingredient(
    ingredient_id: str,
    payload: IngredientUpdateRequest,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.update_ingredient(
        user_id=user_id,
        ingredient_id=ingredient_id,
        payload=payload.model_dump(exclude_unset=True),
        db=db,
    )
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.delete("/fridge/ingredients/{ingredient_id}")
async def delete_ingredient(
    ingredient_id: str,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.delete_ingredient(user_id, ingredient_id, db)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.post("/fridge/scan")
async def create_scan(
    request: Request,
    db: db_dep,
    minio: minio_dep,
    user_id: str = Depends(get_current_user_id),
    file: UploadFile = File(...),
    captured_at: datetime | None = Form(None),
):
    content = await file.read()
    object_key = f"fridge/{user_id}/{datetime.utcnow().isoformat()}_{file.filename or 'photo'}"
    await minio.upload_bytes(object_key, content)

    data = await AppBffService.create_scan_job(
        user_id=user_id,
        object_key=object_key,
        captured_at=captured_at,
        db=db,
    )

    if settings.DATABASE_URL.endswith(":memory:"):
        await AppBffService.complete_scan_job_inline(data["scan_id"], db)
    else:
        asyncio.create_task(fridge_recognition.process_job(data["scan_id"]))

    return envelope(data, getattr(request.state, "trace_id", ""))


@router.get("/fridge/scan/{scan_id}")
async def get_scan(
    scan_id: str,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.get_scan(user_id, scan_id, db)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.get("/fridge/scan/{scan_id}/events")
async def scan_events(
    scan_id: str,
    request: Request,
    db: db_dep,
    redis: redis_dep,
    user_id: str = Depends(get_current_user_id),
):
    current = await AppBffService.get_scan(user_id, scan_id, db)
    key = f"fridge:recognition:events:{scan_id}"

    if current["status"] in {"success", "failed"}:
        final_payload = current["result"] if current["status"] == "success" else {"error": current["error"]}

        async def done_stream() -> AsyncGenerator[str, None]:
            yield sse_event("final", final_payload)

        return StreamingResponse(done_stream(), media_type="text/event-stream")

    async def event_stream() -> AsyncGenerator[str, None]:
        while True:
            if await request.is_disconnected():
                return

            payload = await redis.lpop(key)
            if payload:
                try:
                    event_obj = json.loads(payload)
                except json.JSONDecodeError:
                    yield sse_event("message", {"raw": payload})
                    continue

                event_name = event_obj.get("event", "message")
                event_data = event_obj.get("data")
                yield sse_event(event_name, event_data)
                if event_name == "final":
                    return
            else:
                await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/fridge/scan/{scan_id}/apply")
async def apply_scan(
    scan_id: str,
    payload: ScanApplyRequest,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.apply_scan(user_id, scan_id, payload.merge_by_name, db)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.post("/home-chef/recipes/generate")
async def generate_home_chef_recipes(
    payload: HomeChefRecipeGenerateRequest,
    request: Request,
    db: db_dep,
    redis: redis_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.generate_home_chef_recipes(
        user_id=user_id,
        payload=payload.model_dump(exclude_unset=True),
        db=db,
        redis_client=redis,
    )
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.get("/today/card")
async def get_today_card(
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.get_today_card(user_id, db)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.get("/restaurants")
async def list_restaurants(
    request: Request,
    redis: redis_dep,
    _user_id: str = Depends(get_current_user_id),
    q: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    lat: float | None = Query(default=None),
    lng: float | None = Query(default=None),
):
    data = await AppBffService.list_restaurants(
        redis_client=redis,
        q=q,
        tag=tag,
        lat=lat,
        lng=lng,
        sort=sort,
    )
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.get("/restaurants/{provider}/{provider_id}")
async def get_restaurant_detail(
    provider: str,
    provider_id: str,
    request: Request,
    db: db_dep,
    redis: redis_dep,
    _user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.restaurant_detail(provider, provider_id, db, redis)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.post("/games/blind-box/draw")
async def blind_box_draw(
    payload: BlindBoxDrawRequest,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.blind_box_draw(user_id=user_id, seed=payload.seed, db=db)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.get("/games/wheel/current")
async def get_wheel_current(
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.get_wheel_current(user_id, db)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.put("/games/wheel/current")
async def upsert_wheel_current(
    payload: WheelCurrentUpdateRequest,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.upsert_wheel_current(user_id, payload.model_dump(), db)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.post("/games/wheel/current/spin")
async def spin_wheel_current(
    payload: WheelSpinRequest,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.spin_wheel_current(user_id, payload.seed, db)
    return envelope(data, getattr(request.state, "trace_id", ""))


# Chat session endpoints
@router.post("/chat/session")
async def create_chat_session(
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.create_chat_session(user_id, db)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.get("/chat/sessions")
async def list_chat_sessions(
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    q: str | None = Query(default=None),
):
    data = await AppBffService.list_chat_sessions(user_id, db, limit, offset, q)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.get("/chat/session/{session_id}/messages")
async def list_chat_messages(
    session_id: str,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    data = await AppBffService.list_chat_messages(user_id, session_id, db, limit, offset)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.patch("/chat/session/{session_id}")
async def rename_chat_session(
    session_id: str,
    payload: ChatSessionUpdateRequest,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    if not payload.title:
        return envelope({"updated": False}, getattr(request.state, "trace_id", ""))
    data = await AppBffService.rename_chat_session(user_id, session_id, payload.title, db)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.delete("/chat/session/{session_id}")
async def delete_chat_session(
    session_id: str,
    request: Request,
    db: db_dep,
    redis: redis_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.delete_chat_session(user_id, session_id, db, redis)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.post("/chat/session/{session_id}/stop")
async def stop_chat(
    session_id: str,
    request: Request,
    db: db_dep,
    redis: redis_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await AppBffService.stop_chat_session(user_id, session_id, db, redis)
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.post("/chat/session/{session_id}/stream")
async def chat_stream(
    session_id: str,
    request: Request,
    payload: ChatSessionStreamRequest | None,
    db: db_dep,
    redis: redis_dep,
    user_id: str = Depends(get_current_user_id),
):
    raw = payload.model_dump(exclude_unset=True) if payload else {}
    state = await AppBffService.prepare_chat_stream_state(
        session_id=session_id,
        user_id=user_id,
        payload=raw,
        db=db,
        redis_client=redis,
        forwarded_for=request.headers.get("x-forwarded-for"),
        real_ip=request.headers.get("x-real-ip"),
        request_client_host=request.client.host if request.client else None,
        trace_id=getattr(request.state, "trace_id", None),
        rate_limit_key_prefix="app_chat",
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        async for item in run_chat_stream(request, db, redis, state):
            yield sse_event(item["event"], item["data"])

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
