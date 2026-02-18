# smart-eats-ai-bakend

吃点啥智能系统（FastAPI + LangGraph + SSE + React 前端）。

## 生产部署（Docker + HTTPS）

- 详细教程见：[`deploy/DEPLOYMENT_GUIDE.md`](./deploy/DEPLOYMENT_GUIDE.md)
- 一键部署：

```bash
cp .env.prod.example .env.prod
# 编辑 .env.prod（至少设置 DOMAIN、POSTGRES_PASSWORD、JWT_SECRET、APP_API_BASE_URL）

# 1) 部署服务
make deploy

# 2) 开启 HTTPS（单域名默认模式）
make https-enable

# 3) 续期证书
make https-renew
```

## 开发环境（Conda）

1. 创建并激活环境：

```bash
conda create -n smart-eats python=3.11
conda activate smart-eats
```

2. 安装依赖：

```bash
pip install -r requirements.txt
```

## 配置（本地 Redis + SQLite）

在项目根目录创建并填写 `.env`（参考 `.env.example`）。最小可用配置如下：

```env
APP_NAME=smart-eats
ENV=development
DATABASE_URL=sqlite+aiosqlite:///./local.db
REDIS_URL=redis://127.0.0.1:6379/0
JWT_SECRET=change-me
JWT_ISSUER=smart-eats
JWT_AUDIENCE=smart-eats-clients
JWT_ALG=HS256
ACCESS_TOKEN_TTL_SECONDS=900
REFRESH_TOKEN_TTL_SECONDS=604800
CHAT_CANCEL_TTL=600
LLM_PROVIDER=openai
OPENAI_API_KEY=your_key_here
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL_PLANNER=gpt-4o-mini
OPENAI_MODEL_WRITER=gpt-4o-mini
```

## 本地 Redis 启动

```bash
brew services start redis
```

或使用 Docker：

```bash
docker run -d --name smart-eats-redis -p 6379:6379 redis:7
```

## 启动服务

```bash
uvicorn app.main:app --reload
```

浏览器打开：`http://127.0.0.1:8000/`（自带简单前端测试页）。

## 开发运行说明

- **数据库**：默认 SQLite（`local.db`），适合本地开发。
- **Redis**：本地 Redis 用于 SSE stop、限流、缓存等。
- **LLM**：默认 OpenAI；可在 `.env` 配置其他 provider（DeepSeek/Qwen）。
- **Agent 切换**：请求体支持 `agent_type`（如 `chat`/`today`/`fridge`）。

## 常见问题

- **LLM 未生效**：启动日志中 `openai_key_set=False` 说明没有读到 `.env` 或未重启服务。
- **SSE 断开**：确认 Redis 已启动，`REDIS_URL` 正确。

## Tests

```bash
pytest -q
```

默认使用 sqlite+aiosqlite（无需外部 Postgres）。
