from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.models.base import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    health_goal: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    height: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    dietary_style: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserPreference(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    taste_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    avoid_ingredients: Mapped[list[str]] = mapped_column(JSON, default=list)
    allergens: Mapped[list[str]] = mapped_column(JSON, default=list)
    spicy_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
