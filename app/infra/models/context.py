from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.models.base import Base


class ContextSnapshot(Base):
    __tablename__ = "context_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
