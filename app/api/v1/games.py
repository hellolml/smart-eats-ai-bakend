from __future__ import annotations

import random
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import db_dep, get_current_user_id
from app.common.errors import envelope
from app.infra.models.game import BlindboxRoll, WheelConfig, WheelSpin
from app.infra.models.preference import UserPreference

router = APIRouter()


class BlindboxRequest(BaseModel):
    seed: str | None = None


class WheelOption(BaseModel):
    label: str


class WheelConfigCreate(BaseModel):
    name: str
    options: list[WheelOption]


class WheelConfigUpdate(BaseModel):
    name: str | None = None
    options: list[WheelOption] | None = None


class WheelSpinRequest(BaseModel):
    seed: str | None = None


@router.post("/blindbox/roll")
async def blindbox_roll(
    payload: BlindboxRequest,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    pool = ["noodles", "dumplings", "salad", "soup", "rice bowl"]
    pref_result = await db.execute(
        select(UserPreference).where(UserPreference.user_id == user_id)
    )
    pref = pref_result.scalar_one_or_none()
    avoid = set((pref.avoid_ingredients or []) + (pref.allergens or [])) if pref else set()
    filtered = [item for item in pool if item not in avoid]
    warnings: list[str] = []
    if filtered:
        pool = filtered
    else:
        warnings.append("avoid list filtered all items; fallback to full pool")
    seed = payload.seed or str(uuid4())
    rng = random.Random(seed)
    result = rng.choice(pool)

    roll = BlindboxRoll(
        id=str(uuid4()),
        user_id=user_id,
        result=result,
        seed=seed,
    )
    db.add(roll)
    await db.commit()

    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"result": result, "seed": seed, "warnings": warnings}, trace_id)


@router.get("/wheels")
async def list_wheels(
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(select(WheelConfig).where(WheelConfig.user_id == user_id))
    configs = result.scalars().all()
    data = [
        {"id": cfg.id, "name": cfg.name, "options": cfg.options}
        for cfg in configs
    ]
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(data, trace_id)


@router.post("/wheels")
async def create_wheel(
    payload: WheelConfigCreate,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    options = [opt.model_dump() for opt in payload.options]
    cfg = WheelConfig(
        id=str(uuid4()),
        user_id=user_id,
        name=payload.name,
        options={"options": options},
    )
    db.add(cfg)
    await db.commit()
    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"id": cfg.id, "name": cfg.name, "options": cfg.options}, trace_id)


@router.patch("/wheels/{wheel_id}")
async def update_wheel(
    wheel_id: str,
    payload: WheelConfigUpdate,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(
        select(WheelConfig).where(WheelConfig.id == wheel_id, WheelConfig.user_id == user_id)
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status_code=404, detail="wheel not found")

    if payload.name is not None:
        cfg.name = payload.name
    if payload.options is not None:
        cfg.options = {"options": [opt.model_dump() for opt in payload.options]}

    await db.commit()
    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"id": cfg.id, "name": cfg.name, "options": cfg.options}, trace_id)


@router.delete("/wheels/{wheel_id}")
async def delete_wheel(
    wheel_id: str,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(
        select(WheelConfig).where(WheelConfig.id == wheel_id, WheelConfig.user_id == user_id)
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status_code=404, detail="wheel not found")
    await db.delete(cfg)
    await db.commit()
    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"deleted": True}, trace_id)


@router.post("/wheels/{wheel_id}/spin")
async def spin_wheel(
    wheel_id: str,
    payload: WheelSpinRequest,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(
        select(WheelConfig).where(WheelConfig.id == wheel_id, WheelConfig.user_id == user_id)
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status_code=404, detail="wheel not found")

    options = cfg.options.get("options") if isinstance(cfg.options, dict) else cfg.options
    options = options or []
    if not options:
        raise HTTPException(status_code=400, detail="wheel has no options")

    pref_result = await db.execute(
        select(UserPreference).where(UserPreference.user_id == user_id)
    )
    pref = pref_result.scalar_one_or_none()
    avoid = set((pref.avoid_ingredients or []) + (pref.allergens or [])) if pref else set()

    def _is_allowed(option: Any) -> bool:
        label = option.get("label") if isinstance(option, dict) else str(option)
        lowered = label.lower()
        return all(term.lower() not in lowered for term in avoid)

    filtered_options = [opt for opt in options if _is_allowed(opt)]
    warnings: list[str] = []
    if filtered_options:
        options = filtered_options
    else:
        warnings.append("avoid list filtered all options; fallback to full options")

    seed = payload.seed or str(uuid4())
    rng = random.Random(seed)
    selected = rng.choice(options)
    angle = rng.random() * 360

    spin = WheelSpin(
        id=str(uuid4()),
        user_id=user_id,
        config_id=cfg.id,
        result=selected.get("label") if isinstance(selected, dict) else str(selected),
        seed=seed,
        angle=angle,
    )
    db.add(spin)
    await db.commit()

    trace_id = getattr(request.state, "trace_id", "")
    return envelope(
        {
            "selected_option": selected,
            "angle": angle,
            "seed": seed,
            "warnings": warnings,
        },
        trace_id,
    )
