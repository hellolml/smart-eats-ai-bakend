from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.models.base import Base


class GroceryList(Base):
    __tablename__ = "grocery_lists"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(255))
    source_recipe: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GroceryListItem(Base):
    __tablename__ = "grocery_list_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    list_id: Mapped[str] = mapped_column(String(36), ForeignKey("grocery_lists.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    checked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
