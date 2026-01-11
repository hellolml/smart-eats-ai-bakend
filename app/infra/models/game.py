from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.models.base import Base


class WheelConfig(Base):
    __tablename__ = "wheel_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(64))
    options: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WheelSpin(Base):
    __tablename__ = "wheel_spins"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    config_id: Mapped[str] = mapped_column(String(36), index=True)
    result: Mapped[str] = mapped_column(String(255))
    seed: Mapped[str | None] = mapped_column(String(64), nullable=True)
    angle: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BlindboxRoll(Base):
    __tablename__ = "blindbox_rolls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    result: Mapped[str] = mapped_column(String(255))
    seed: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
