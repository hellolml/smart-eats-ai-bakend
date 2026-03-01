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
    CHAT_HISTORY_LIMIT: int = 30
    CHAT_HISTORY_CACHE_LIMIT: int = 120
    CHAT_HISTORY_CACHE_TTL_SECONDS: int = 300
    CHAT_HISTORY_LOCAL_CACHE_SIZE: int = 256
    CHAT_HISTORY_LOCAL_CACHE_TTL_SECONDS: int = 30
    CHAT_HISTORY_CACHE_MODE: str = "local_validate"
    TOOL_HISTORY_KEEP: int = 3
    TOOL_HISTORY_ALLOW: str = "get_ip_location,geocode_location,search_restaurants,plan_route,get_weather,rag_search_recipes,search_recipes"
    CHAT_COMPACT_MIN_MESSAGES: int = 3
    CHAT_COMPACT_TRIGGER_RATIO: float = 0.9
    CHAT_COMPACT_TAIL_RATIO: float = 0.2
    LLM_MODEL_CONTEXT_SIZE: int = 8192
    CONTEXT_SNAPSHOT_TTL_SECONDS: int = 600
    MINIO_BUCKET: str = "smart-eats"
    MINIO_BASE_PATH: str = ".minio_stub"
    RECOGNITION_EVENT_TTL_SECONDS: int = 600
    OPENAI_API_KEY: str | None = None
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL_PLANNER: str = "gpt-4o-mini"
    OPENAI_MODEL_WRITER: str = "gpt-4o-mini"
    LLM_PROVIDER: str = "qwen"
    LLM_PROVIDERS: str = "qwen"
    LLM_MODELS: str = "qwen:qwen3.5-flash,qwen:qwen3.5-plus,qwen:qwen3.5-flash-2026-02-23,qwen:qwen3.5-plus-2026-02-15,qwen:qwen3.5-397b-a17b"
    DEEPSEEK_API_KEY: str | None = None
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL_PLANNER: str = "deepseek-chat"
    DEEPSEEK_MODEL_WRITER: str = "deepseek-chat"
    DASHSCOPE_API_KEY: str | None = None
    QWEN_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    QWEN_MODEL_PLANNER: str = "qwen3.5-flash"
    QWEN_MODEL_WRITER: str = "qwen3.5-flash"
    LLM_REQUEST_LOG: str = "none"
    AGENT_MAX_STEPS: int = 6
    AGENT_GRAPH_RUNTIME: str = "legacy"
    LANGGRAPH_CHECKPOINT_DB: str = ".langgraph_checkpoints.sqlite"
    LANGGRAPH_DURABILITY: str = "async"
    LANGGRAPH_CHECKPOINT_BACKEND: str = "sqlite"
    CHAT_PAUSE_TTL: int = 600
    SEED_DEMO_USER_ID: str | None = None
    AMAP_SEARCH_CACHE_TTL_SECONDS: int = 180
    RESTAURANT_DETAIL_CACHE_TTL_SECONDS: int = 300
    MCP_SERVERS_CONFIG_PATH: str | None = "mcp_servers.json"
    APP_FALLBACK_ENABLED: bool = True
    APP_RECIPE_GEN_TIMEOUT_SECONDS: int = 12


settings = Settings()
