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
    EVAL_DATABASE_URL: str | None = None
    REDIS_URL: str = "redis://localhost:6379/0"

    JWT_SECRET: str = "change-me"
    JWT_ISSUER: str = "smart-eats"
    JWT_AUDIENCE: str = "smart-eats-clients"
    JWT_ALG: str = "HS256"

    ACCESS_TOKEN_TTL_SECONDS: int = 15 * 60
    REFRESH_TOKEN_TTL_SECONDS: int = 7 * 24 * 60 * 60
    REFRESH_COOKIE_NAME: str = "se_refresh_token"
    REFRESH_COOKIE_SECURE: bool = False
    REFRESH_COOKIE_SAMESITE: str = "lax"
    CSRF_COOKIE_NAME: str = "se_csrf_token"
    CSRF_HEADER_NAME: str = "x-csrf-token"

    GITHUB_OAUTH_CLIENT_ID: str | None = None
    GITHUB_OAUTH_CLIENT_SECRET: str | None = None
    GITHUB_OAUTH_REDIRECT_URI: str | None = None

    SMS_PROVIDER: str = "mock"  # mock | webhook
    SMS_WEBHOOK_URL: str | None = None
    SMS_WEBHOOK_TOKEN: str | None = None
    SMS_SIGN_NAME: str | None = None
    SMS_TEMPLATE_CODE: str | None = None

    EMAIL_PROVIDER: str = "mock"  # mock | smtp | webhook
    EMAIL_WEBHOOK_URL: str | None = None
    EMAIL_WEBHOOK_TOKEN: str | None = None
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASS: str | None = None
    SMTP_FROM: str | None = None
    SMTP_USE_TLS: bool = True

    ONECLICK_PROVIDER: str = "mock"  # mock | webhook
    ONECLICK_WEBHOOK_URL: str | None = None
    ONECLICK_WEBHOOK_TOKEN: str | None = None

    CHAT_CANCEL_TTL: int = 600
    CHAT_HISTORY_LIMIT: int = 30
    CHAT_HISTORY_CACHE_LIMIT: int = 120
    CHAT_HISTORY_CACHE_TTL_SECONDS: int = 300
    CHAT_HISTORY_LOCAL_CACHE_SIZE: int = 256
    CHAT_HISTORY_LOCAL_CACHE_TTL_SECONDS: int = 30
    CHAT_HISTORY_CACHE_MODE: str = "local_validate"
    CHAT_ATTACHMENT_MAX_BYTES: int = 8 * 1024 * 1024
    LLM_VISION_ENABLED: bool = True
    LLM_VISION_PROVIDER: str | None = None
    LLM_VISION_MODEL_PLANNER: str | None = None
    LLM_VISION_MAX_IMAGES: int = 4
    LLM_VISION_MAX_IMAGE_BYTES: int = 8 * 1024 * 1024
    TOOL_HISTORY_KEEP: int = 3
    TOOL_HISTORY_ALLOW: str = "get_ip_location,geocode_location,search_restaurants,plan_route,get_weather,rag_search_recipes,search_recipes,food_decision"
    CHAT_COMPACT_MIN_MESSAGES: int = 3
    CHAT_COMPACT_TRIGGER_RATIO: float = 0.8
    CHAT_COMPACT_HARD_RATIO: float = 0.92
    CHAT_COMPACT_TAIL_RATIO: float = 0.2
    CHAT_COMPACT_RESERVED_OUTPUT_TOKENS: int = 8000
    CHAT_COMPACT_RESERVED_TOOL_TOKENS: int = 16000
    CHAT_COMPACT_MAX_ATTEMPTS: int = 2
    CHAT_COMPACT_MIN_REDUCTION_RATIO: float = 0.05
    LLM_MODEL_CONTEXT_SIZE: int = 128000
    LLM_MODEL_CONTEXT_WINDOWS: str = "qwen:qwen3.5-flash=128000,qwen:qwen3.5-plus=128000,deepseek:deepseek-chat=64000,gpt-4.1=1047576,gpt-5=400000"
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
    LLM_PROMPT_CACHE_ENABLED: bool = True
    LLM_PLANNER_REQUEST_TIMEOUT_SECONDS: int = 90
    LLM_INTENT_REQUEST_TIMEOUT_SECONDS: int = 20
    LLM_CONFIG_ENCRYPTION_KEY: str | None = None
    LLM_CONFIG_TEST_TIMEOUT_SECONDS: int = 15
    AGENT_MAX_STEPS: int = 6
    AGENT_SKILLS_ENABLED: bool = True
    AGENT_SKILLS_PATH: str = "agent_skills"
    AGENT_SKILLS_MAX_ACTIVE: int = 3
    AGENT_SKILLS_MAX_PROMPT_CHARS: int = 80000
    AGENT_SKILLS_LOG_DIAGNOSTICS: bool = True
    LANGGRAPH_CHECKPOINT_DB: str = ".langgraph_checkpoints.sqlite"
    LANGGRAPH_DURABILITY: str = "async"
    LANGGRAPH_CHECKPOINT_BACKEND: str = "sqlite"
    LANGGRAPH_STORE_BACKEND: str = "postgres"
    LANGGRAPH_STORE_DB: str | None = None
    CHAT_PAUSE_TTL: int = 600
    SEED_DEMO_USER_ID: str | None = None
    AMAP_API_KEY: str | None = None
    AMAP_SEARCH_CACHE_TTL_SECONDS: int = 180
    TRAVEL_POI_CACHE_TTL_SECONDS: int = 604800
    USER_PREFERENCE_MD_DIR: str = ".user_preferences"
    RESTAURANT_DETAIL_CACHE_TTL_SECONDS: int = 300
    MCP_SERVERS_CONFIG_PATH: str | None = "mcp_servers.json"
    APP_FALLBACK_ENABLED: bool = True
    APP_RECIPE_GEN_TIMEOUT_SECONDS: int = 12
    APP_AUTH_PASSWORD_ENABLED: bool = True
    APP_AUTH_REGISTER_ENABLED: bool = True
    APP_AUTH_OTP_ENABLED: bool = False
    APP_AUTH_ONECLICK_ENABLED: bool = False
    APP_AUTH_GITHUB_OAUTH_ENABLED: bool = False
    APP_AUTH_PASSWORD_RESET_ENABLED: bool = False
    APP_AUTH_PHONE_ENABLED: bool = True
    APP_AUTH_EMAIL_ENABLED: bool = True
    REALTIME_EVAL_ENABLED: bool = False
    REALTIME_EVAL_SAMPLE_RATE: float = 0.1
    REALTIME_EVAL_DEEP_JUDGE_ENABLED: bool = False
    REALTIME_EVAL_RETENTION_DAYS: int = 30
    # ── Alert notification ──
    ALERT_WEBHOOK_URL: str | None = None
    ALERT_WEBHOOK_TYPE: str = "generic"  # generic | feishu | slack
    ALERT_WEBHOOK_SECRET: str | None = None  # Optional signing secret


settings = Settings()
