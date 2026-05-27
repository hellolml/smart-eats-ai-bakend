from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, TypeAdapter, ValidationInfo, field_validator, model_validator


PHONE_CN_RE = re.compile(r"^1[3-9]\d{9}$")


def _is_valid_cn_phone(value: str) -> bool:
    return bool(PHONE_CN_RE.fullmatch(value.strip()))


def _is_valid_email(value: str) -> bool:
    try:
        TypeAdapter(EmailStr).validate_python(value)
        return True
    except Exception:
        return False


def _validate_password_strength(value: str) -> str:
    if not (8 <= len(value) <= 64):
        raise ValueError("password length must be between 8 and 64")
    if not re.search(r"[A-Za-z]", value):
        raise ValueError("password must contain at least one letter")
    if not re.search(r"\d", value):
        raise ValueError("password must contain at least one number")
    return value


class RegisterRequest(BaseModel):
    email: EmailStr | None = None
    phone: str | None = None
    password: str
    name: str | None = None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str | None):
        if value is None:
            return value
        clean = value.strip()
        if not _is_valid_cn_phone(clean):
            raise ValueError("phone must match ^1[3-9]\\d{9}$")
        return clean

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str):
        return _validate_password_strength(value)


class RegisterOtpRequest(BaseModel):
    email: EmailStr | None = None
    phone: str | None = None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str | None):
        if value is None:
            return value
        clean = value.strip()
        if not _is_valid_cn_phone(clean):
            raise ValueError("phone must match ^1[3-9]\\d{9}$")
        return clean

    @model_validator(mode="after")
    def validate_identity(self):
        if not self.email and not self.phone:
            raise ValueError("email or phone required")
        return self


class RegisterConfirmRequest(BaseModel):
    email: EmailStr | None = None
    phone: str | None = None
    code: str
    password: str
    name: str | None = None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str | None):
        if value is None:
            return value
        clean = value.strip()
        if not _is_valid_cn_phone(clean):
            raise ValueError("phone must match ^1[3-9]\\d{9}$")
        return clean

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str):
        return _validate_password_strength(value)

    @model_validator(mode="after")
    def validate_identity(self):
        if not self.email and not self.phone:
            raise ValueError("email or phone required")
        return self


class LoginRequest(BaseModel):
    account: str
    password: str

    @field_validator("account")
    @classmethod
    def validate_account(cls, value: str):
        clean = value.strip()
        if not (_is_valid_cn_phone(clean) or _is_valid_email(clean)):
            raise ValueError("account must be a valid phone or email")
        return clean

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
    refresh_token: str | None = None

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
    refresh_token: str | None = None

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

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str):
        return _validate_password_strength(value)


class PasswordResetRequestRequest(BaseModel):
    account: str

    @field_validator("account")
    @classmethod
    def validate_account(cls, value: str):
        clean = value.strip()
        if not (_is_valid_cn_phone(clean) or _is_valid_email(clean)):
            raise ValueError("account must be a valid phone or email")
        return clean


class PasswordResetConfirmRequest(BaseModel):
    account: str
    code: str
    new_password: str

    @field_validator("account")
    @classmethod
    def validate_account(cls, value: str):
        clean = value.strip()
        if not (_is_valid_cn_phone(clean) or _is_valid_email(clean)):
            raise ValueError("account must be a valid phone or email")
        return clean

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str):
        return _validate_password_strength(value)


class OAuthCallbackRequest(BaseModel):
    code: str
    state: str


class OtpLoginRequest(BaseModel):
    account: str

    @field_validator("account")
    @classmethod
    def validate_account(cls, value: str):
        clean = value.strip()
        if not (_is_valid_cn_phone(clean) or _is_valid_email(clean)):
            raise ValueError("account must be a valid phone or email")
        return clean


class OtpLoginConfirmRequest(BaseModel):
    account: str
    code: str

    @field_validator("account")
    @classmethod
    def validate_account(cls, value: str):
        clean = value.strip()
        if not (_is_valid_cn_phone(clean) or _is_valid_email(clean)):
            raise ValueError("account must be a valid phone or email")
        return clean


class OneClickLoginRequest(BaseModel):
    token: str


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
    scene: str | None = None
    attachments: list[dict[str, Any]] | None = None
    client_context_overrides: dict[str, Any] | None = None
    provider: str | None = None
    model: str | None = None


class ChatSessionCreateRequest(BaseModel):
    title: str | None = None
    scene: str | None = None


class ChatSessionUpdateRequest(BaseModel):
    title: str | None = None
