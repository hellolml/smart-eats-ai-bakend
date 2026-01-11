from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[Path(__file__).resolve().parents[2] / ".env"],
        env_file_encoding="utf-8",
        extra="allow",
    )

    APP_NAME: str = "smart-eats"
    ENV: str = "development"
    DEBUG: bool = True
    DEFAULT_LANGUAGE: str = "zh"

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/smart_eats"
    REDIS_URL: str = "redis://localhost:6379/0"

    JWT_SECRET: str = "change-me"
    JWT_ISSUER: str = "smart-eats"
    JWT_AUDIENCE: str = "smart-eats-clients"
    JWT_ALG: str = "HS256"

    ACCESS_TOKEN_TTL_SECONDS: int = 15 * 60
    REFRESH_TOKEN_TTL_SECONDS: int = 7 * 24 * 60 * 60

    CHAT_CANCEL_TTL: int = 600
    CONTEXT_SNAPSHOT_TTL_SECONDS: int = 600
    MINIO_BUCKET: str = "smart-eats"
    MINIO_BASE_PATH: str = ".minio_stub"
    RECOGNITION_EVENT_TTL_SECONDS: int = 600
    OPENAI_API_KEY: str | None = None
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL_PLANNER: str = "gpt-4o-mini"
    OPENAI_MODEL_WRITER: str = "gpt-4o-mini"
    LLM_PROVIDER: str = "qwen"
    LLM_PROVIDERS: str = "openai,deepseek,qwen"
    DEEPSEEK_API_KEY: str | None = None
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL_PLANNER: str = "deepseek-chat"
    DEEPSEEK_MODEL_WRITER: str = "deepseek-chat"
    DASHSCOPE_API_KEY: str | None = None
    QWEN_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    QWEN_MODEL_PLANNER: str = "qwen-plus"
    QWEN_MODEL_WRITER: str = "qwen-plus"
    AGENT_MAX_STEPS: int = 4
    LANGGRAPH_CHECKPOINT_DB: str = ".langgraph_checkpoints.sqlite"
    LANGGRAPH_DURABILITY: str = "async"
    CHAT_PAUSE_TTL: int = 600


settings = Settings()
