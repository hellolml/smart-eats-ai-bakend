from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, ValidationInfo, field_validator, model_validator


class RegisterRequest(BaseModel):
    email: EmailStr | None = None
    phone: str | None = None
    password: str
    name: str | None = None


class LoginRequest(BaseModel):
    account: str
    password: str

    @model_validator(mode="before")
    @classmethod
    def map_legacy_account_fields(cls, data: Any):
        if not isinstance(data, dict):
            return data
        if data.get("account"):
            return data

        # Backward-compatible mapping for frontend payloads that still send phone/email.
        if data.get("phone"):
            mapped = dict(data)
            mapped["account"] = data["phone"]
            return mapped
        if data.get("email"):
            mapped = dict(data)
            mapped["account"] = data["email"]
            return mapped
        return data


class RefreshRequest(BaseModel):
    refresh_token: str

    @model_validator(mode="before")
    @classmethod
    def map_legacy_refresh_token(cls, data: Any):
        if not isinstance(data, dict):
            return data
        if data.get("refresh_token"):
            return data
        if data.get("refreshToken"):
            mapped = dict(data)
            mapped["refresh_token"] = data["refreshToken"]
            return mapped
        return data


class LogoutRequest(BaseModel):
    refresh_token: str

    @model_validator(mode="before")
    @classmethod
    def map_legacy_refresh_token(cls, data: Any):
        if not isinstance(data, dict):
            return data
        if data.get("refresh_token"):
            return data
        if data.get("refreshToken"):
            mapped = dict(data)
            mapped["refresh_token"] = data["refreshToken"]
            return mapped
        return data


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class UpdateMeRequest(BaseModel):
    name: str | None = None
    avatar: str | None = None
    health_goal: str | None = None
    current_state: str | None = None


class GoalStateUpdateRequest(BaseModel):
    health_goal: str | None = None
    current_state: str | None = None


class MePreferencesUpdateRequest(BaseModel):
    tastes: list[str] | None = None
    taboos: list[str] | None = None
    allergens: list[str] | None = None
    spicy_level: int | None = None
    budget_level: int | None = None


class IngredientCreateRequest(BaseModel):
    name: str
    quantity: float | None = None
    unit: str | None = None
    expiry_date: datetime | None = None
    source: str = "manual"


class IngredientUpdateRequest(BaseModel):
    name: str | None = None
    quantity: float | None = None
    unit: str | None = None
    expiry_date: datetime | None = None
    source: str | None = None


class ScanApplyRequest(BaseModel):
    merge_by_name: bool = True


class HomeChefRecipeGenerateRequest(BaseModel):
    ingredients: list[str] | None = None
    count: int = Field(default=2, ge=1, le=5)


class RestaurantsQuery(BaseModel):
    q: str | None = None
    sort: Literal["nearest", "rating_desc", "price_asc"] | None = None
    tag: str | None = None
    lat: float | None = None
    lng: float | None = None

    @field_validator("q", "tag", mode="before")
    @classmethod
    def normalize_text(cls, value: Any):
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @field_validator("lat", "lng")
    @classmethod
    def validate_coordinate_range(cls, value: float | None, info: ValidationInfo):
        if value is None:
            return value
        if info.field_name == "lat" and not (-90 <= value <= 90):
            raise ValueError("lat must be between -90 and 90")
        if info.field_name == "lng" and not (-180 <= value <= 180):
            raise ValueError("lng must be between -180 and 180")
        return value

    @model_validator(mode="after")
    def validate_coordinate_pair(self):
        if (self.lat is None) != (self.lng is None):
            raise ValueError("lat and lng must be provided together")
        return self


class BlindBoxDrawRequest(BaseModel):
    seed: str | None = None


class WheelCurrentUpdateRequest(BaseModel):
    name: str = "我的转盘"
    options: list[str]


class WheelSpinRequest(BaseModel):
    seed: str | None = None


class ChatSessionStreamRequest(BaseModel):
    message: str | None = None
    client_context_overrides: dict[str, Any] | None = None
    provider: str | None = None
    model: str | None = None
    agent_type: str | None = None


class ChatSessionUpdateRequest(BaseModel):
    title: str | None = None
