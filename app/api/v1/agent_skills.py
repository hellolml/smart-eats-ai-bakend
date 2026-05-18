from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel

from app.agent.skills.importer.validator import SkillValidationError
from app.agent.skills.management import SkillManagementService
from app.api.deps import get_current_user_id
from app.common.config import settings
from app.common.errors import INVALID_PARAMS, envelope

router = APIRouter()


class SkillImportDirectoryRequest(BaseModel):
    source_path: str


class SkillImportUrlRequest(BaseModel):
    url: str


def _service() -> SkillManagementService:
    return SkillManagementService(settings.AGENT_SKILLS_PATH)


@router.get("/skills")
async def list_agent_skills(request: Request, _user_id: str = Depends(get_current_user_id)):
    data = {"skills": _service().list_skills()}
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.post("/skills/import/directory")
async def import_agent_skill_from_directory(
    payload: SkillImportDirectoryRequest,
    request: Request,
    _user_id: str = Depends(get_current_user_id),
):
    try:
        data = _service().import_from_directory(payload.source_path)
    except SkillValidationError as exc:
        return envelope({}, getattr(request.state, "trace_id", ""), code=INVALID_PARAMS, message=str(exc))
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.post("/skills/import/url")
async def import_agent_skill_from_url(
    payload: SkillImportUrlRequest,
    request: Request,
    _user_id: str = Depends(get_current_user_id),
):
    try:
        data = _service().import_from_url(payload.url)
    except SkillValidationError as exc:
        return envelope({}, getattr(request.state, "trace_id", ""), code=INVALID_PARAMS, message=str(exc))
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.post("/skills/import/zip")
async def import_agent_skill_from_zip(
    request: Request,
    _user_id: str = Depends(get_current_user_id),
    file: UploadFile = File(...),
):
    content = await file.read()
    try:
        data = _service().import_from_zip_bytes(content, source_name=file.filename or "skill.zip")
    except SkillValidationError as exc:
        return envelope({}, getattr(request.state, "trace_id", ""), code=INVALID_PARAMS, message=str(exc))
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.delete("/skills/{skill_id}")
async def uninstall_agent_skill(
    skill_id: str,
    request: Request,
    _user_id: str = Depends(get_current_user_id),
):
    try:
        data = _service().uninstall(skill_id)
    except SkillValidationError as exc:
        return envelope({}, getattr(request.state, "trace_id", ""), code=INVALID_PARAMS, message=str(exc))
    return envelope(data, getattr(request.state, "trace_id", ""))
