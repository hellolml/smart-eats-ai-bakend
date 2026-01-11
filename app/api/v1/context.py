from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api.deps import db_dep, get_current_user_id, redis_dep
from app.common.errors import envelope
from app.domain.context.service import ContextService

router = APIRouter()


class ContextQuery(BaseModel):
    scene: str = "chat"
    session_id: str | None = None


@router.get("/snapshot")
async def get_snapshot(
    request: Request,
    db: db_dep,
    redis: redis_dep,
    query: ContextQuery = Depends(),
    user_id: str = Depends(get_current_user_id),
):
    snapshot = await ContextService.build(
        db=db,
        redis_client=redis,
        user_id=user_id,
        scene=query.scene,
        session_id=query.session_id,
    )
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(snapshot, trace_id)
