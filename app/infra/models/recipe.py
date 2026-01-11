from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.models.base import Base


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(255))
    image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    cook_time_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    recipe_id: Mapped[str] = mapped_column(String(36), index=True)
    ingredient_name: Mapped[str] = mapped_column(String(128))
    amount: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)


class RecommendationLog(Base):
    __tablename__ = "recommendation_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    scene: Mapped[str] = mapped_column(String(32))
    context_snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    result_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
