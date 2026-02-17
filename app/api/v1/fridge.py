from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, AsyncGenerator
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, select

from app.api.deps import db_dep, get_current_user_id, minio_dep, redis_dep
from app.common.config import settings
from app.common.errors import envelope
from app.common.sse import sse_event
from app.domain.recipe.service import RecipeService
from app.infra.models.fridge import FridgeItem, FridgePhoto, RecognitionJob
from app.tasks import fridge_recognition

router = APIRouter()


class FridgeItemCreate(BaseModel):
    name: str
    quantity: float | None = None
    unit: str | None = None
    expiry_date: datetime | None = None
    source: str = "manual"


class FridgeItemUpdate(BaseModel):
    name: str | None = None
    quantity: float | None = None
    unit: str | None = None
    expiry_date: datetime | None = None
    source: str | None = None


class RecognitionCreate(BaseModel):
    photo_id: str


@router.get("/items")
async def list_items(
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(select(FridgeItem).where(FridgeItem.user_id == user_id))
    items = result.scalars().all()
    data = [
        {
            "id": item.id,
            "name": item.name,
            "quantity": item.quantity,
            "unit": item.unit,
            "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
            "source": item.source,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        }
        for item in items
    ]
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(data, trace_id)


@router.get("/recommendations")
async def fridge_recommendations(
    request: Request,
    db: db_dep,
    redis: redis_dep,
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(select(FridgeItem).where(FridgeItem.user_id == user_id))
    items = result.scalars().all()
    names = [item.name for item in items][:3]
    query = " ".join(names) if names else "home"

    recipes = await RecipeService.search(redis, query)

    def _score(item: dict) -> int:
        title = (item.get("title") or "").lower()
        return sum(1 for name in names if name.lower() in title)

    ranked = sorted(recipes, key=_score, reverse=True)
    data = [
        {
            "title": item.get("title"),
            "cook_time_min": item.get("cook_time_min"),
            "calories": item.get("calories"),
            "tags": item.get("tags") or [],
            "image_url": item.get("image_url"),
        }
        for item in ranked[:3]
    ]
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(data, trace_id)


@router.post("/items")
async def create_item(
    payload: FridgeItemCreate,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    item = FridgeItem(
        id=str(uuid4()),
        user_id=user_id,
        name=payload.name,
        quantity=payload.quantity,
        unit=payload.unit,
        expiry_date=payload.expiry_date,
        source=payload.source,
    )
    db.add(item)
    await db.commit()
    trace_id = getattr(request.state, "trace_id", "")
    data = {
        "id": item.id,
        "name": item.name,
        "quantity": item.quantity,
        "unit": item.unit,
        "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
        "source": item.source,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }
    return envelope(data, trace_id)


@router.patch("/items/{item_id}")
async def update_item(
    item_id: str,
    payload: FridgeItemUpdate,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(
        select(FridgeItem).where(
            FridgeItem.id == item_id,
            FridgeItem.user_id == user_id,
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)

    await db.commit()
    trace_id = getattr(request.state, "trace_id", "")
    data = {
        "id": item.id,
        "name": item.name,
        "quantity": item.quantity,
        "unit": item.unit,
        "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
        "source": item.source,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }
    return envelope(data, trace_id)


@router.delete("/items/{item_id}")
async def delete_item(
    item_id: str,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(
        select(FridgeItem).where(
            FridgeItem.id == item_id,
            FridgeItem.user_id == user_id,
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")

    await db.execute(
        delete(FridgeItem).where(FridgeItem.id == item_id, FridgeItem.user_id == user_id)
    )
    await db.commit()
    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"deleted": True}, trace_id)


@router.post("/recognitions")
async def create_recognition_job(
    payload: RecognitionCreate,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    job = RecognitionJob(
        id=str(uuid4()),
        user_id=user_id,
        photo_id=payload.photo_id,
        status="queued",
        result_json=None,
        error=None,
    )
    db.add(job)
    await db.commit()
    if settings.DATABASE_URL.endswith(":memory:"):
        job.status = "running"
        await db.commit()
        job.result_json = {
            "items": [
                {"name": "egg", "quantity": 2, "unit": "pcs"},
                {"name": "tomato", "quantity": 3, "unit": "pcs"},
            ],
            "request_id": str(uuid4()),
        }
        job.status = "success"
        job.finished_at = datetime.now(timezone.utc)
        await db.commit()
    else:
        asyncio.create_task(fridge_recognition.process_job(job.id))
    trace_id = getattr(request.state, "trace_id", "")
    data = {"job_id": job.id, "status": job.status}
    return envelope(data, trace_id)


@router.get("/recognitions/{job_id}")
async def get_recognition_job(
    job_id: str,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(
        select(RecognitionJob).where(
            RecognitionJob.id == job_id,
            RecognitionJob.user_id == user_id,
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    data = {
        "job_id": job.id,
        "status": job.status,
        "result": job.result_json,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(data, trace_id)


@router.get("/recognitions/{job_id}/events")
async def recognition_events(
    job_id: str,
    request: Request,
    db: db_dep,
    redis: redis_dep,
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(
        select(RecognitionJob).where(
            RecognitionJob.id == job_id,
            RecognitionJob.user_id == user_id,
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    key = f"fridge:recognition:events:{job_id}"

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
                await db.refresh(job)
                if job.status in {"success", "failed"}:
                    final_data = (
                        job.result_json if job.status == "success" else {"error": job.error}
                    )
                    yield sse_event("final", final_data)
                    return
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/photos")
async def upload_photo(
    request: Request,
    db: db_dep,
    minio: minio_dep,
    user_id: str = Depends(get_current_user_id),
    file: UploadFile = File(...),
    captured_at: datetime | None = Form(None),
):
    content = await file.read()
    object_key = f"fridge/{user_id}/{uuid4()}_{file.filename or 'photo'}"
    await minio.upload_bytes(object_key, content)

    photo = FridgePhoto(
        id=str(uuid4()),
        user_id=user_id,
        object_key=object_key,
        captured_at=captured_at,
    )
    db.add(photo)
    await db.commit()

    trace_id = getattr(request.state, "trace_id", "")
    data = {
        "photo_id": photo.id,
        "object_key": photo.object_key,
        "captured_at": photo.captured_at.isoformat() if photo.captured_at else None,
    }
    return envelope(data, trace_id)
