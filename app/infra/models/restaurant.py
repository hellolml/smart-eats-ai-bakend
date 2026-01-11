from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.models.base import Base


class RestaurantCache(Base):
    __tablename__ = "restaurant_cache"
    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_restaurant_provider_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    provider_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(255))
    geo: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RestaurantSearchLog(Base):
    __tablename__ = "restaurant_search_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    query: Mapped[str] = mapped_column(String(255))
    filters_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    geo: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
