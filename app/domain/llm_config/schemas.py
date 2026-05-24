from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ProviderType = Literal["openai_compatible", "anthropic"]
TestStatus = Literal["success", "failed"]


class LlmProviderConfigCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    provider_type: ProviderType = "openai_compatible"
    base_url: str = Field(min_length=1, max_length=512)
    api_key: str = Field(min_length=1, max_length=2048)
    model_planner: str = Field(min_length=1, max_length=120)
    model_writer: str | None = Field(default=None, max_length=120)
    model_vision_planner: str | None = Field(default=None, max_length=120)
    enabled: bool = True
    is_default: bool = False

    @field_validator("display_name", "base_url", "api_key", "model_planner", "model_writer", "model_vision_planner")
    @classmethod
    def strip_strings(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return value.strip()


class LlmProviderConfigUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    provider_type: ProviderType | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    api_key: str | None = Field(default=None, min_length=1, max_length=2048)
    model_planner: str | None = Field(default=None, min_length=1, max_length=120)
    model_writer: str | None = Field(default=None, max_length=120)
    model_vision_planner: str | None = Field(default=None, max_length=120)
    enabled: bool | None = None
    is_default: bool | None = None

    @field_validator("display_name", "base_url", "api_key", "model_planner", "model_writer", "model_vision_planner")
    @classmethod
    def strip_strings(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return value.strip()


class LlmProviderConfigPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    display_name: str
    provider_type: str
    base_url: str
    api_key_hint: str
    model_planner: str
    model_writer: str | None = None
    model_vision_planner: str | None = None
    enabled: bool
    is_default: bool
    last_tested_at: datetime | None = None
    last_test_status: str | None = None
    last_test_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LlmProviderConfigTestRequest(BaseModel):
    config_id: str | None = None
    provider_type: ProviderType = "openai_compatible"
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    api_key: str | None = Field(default=None, min_length=1, max_length=2048)
    model: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("config_id", "base_url", "api_key", "model")
    @classmethod
    def strip_strings(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return value.strip()

    @model_validator(mode="after")
    def validate_source(self):
        if self.config_id:
            return self
        if not self.base_url or not self.api_key:
            raise ValueError("base_url and api_key are required when config_id is absent")
        return self


class LlmProviderConfigTestResult(BaseModel):
    status: TestStatus
    error: str | None = None


class ResolvedModelConfig(BaseModel):
    source: Literal["env", "user_config"]
    provider: str
    provider_value: str | None = None
    config_id: str | None = None
    display_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model_planner: str
    model_writer: str | None = None
    model_vision_planner: str | None = None
