# smart-eats 生产部署教程（Docker Compose + 单域名 HTTPS）

本文档以“**不熟悉项目也能照做部署成功**”为目标，覆盖从 0 到 1 的完整流程，并把关键部署文件以“可复制粘贴”的形式给出。

> 当前推荐/默认部署形态（与我们实际跑通的一致）：
>
>- **一个域名**（例如 `eatwhat.cloud`）
>- `https://<DOMAIN>/` → 前端（frontend 容器）
>- `https://<DOMAIN>/api/...` → 后端（backend 容器，FastAPI）
>- `https://<DOMAIN>/docs` → FastAPI 文档
>
> 后端代码里 API 的总前缀是：`/api/v1`（见 `app/api/v1/router.py`）

---

## 1. 你需要准备什么

### 1.1 服务器

- Linux 服务器（Ubuntu/Debian/OpenCloudOS/CentOS 均可，能装 Docker 即可）
- 推荐配置：2C4G+（后端依赖较重，冷启动会加载 embedding 模型）
- 需要开放端口：
  - **80**（证书签发/HTTP→HTTPS 跳转）
  - **443**（HTTPS）
  - **22**（SSH，默认）

### 1.2 域名 & DNS

你需要 1 个域名，例如：

- `eatwhat.cloud`

在 DNS 服务商处添加 A 记录：

- `eatwhat.cloud` → `你的服务器公网 IP`

> Let’s Encrypt 使用 HTTP-01 校验：必须能从公网访问 `http://eatwhat.cloud/.well-known/acme-challenge/...`

### 1.3 代码

假设项目部署在服务器目录：

- `~/code/smart-eats-ai-bakend`

---

## 2. 服务器安装 Docker（一次性）

以 Ubuntu 为例：

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

echo \
"deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
$(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

sudo usermod -aG docker $USER
# 重新登录 SSH 生效
```

验证：

```bash
docker --version
docker compose version
```

---

## 3. 获取代码

```bash
mkdir -p ~/code
cd ~/code

# 你的仓库地址自行替换
# git clone <git-url> smart-eats-ai-bakend
cd smart-eats-ai-bakend
```

---

## 4. 生产配置：创建 .env.prod

项目自带模板：`.env.prod.example`。

```bash
cd ~/code/smart-eats-ai-bakend
cp .env.prod.example .env.prod
nano .env.prod
```

你至少要改这些：

- `DOMAIN`：你的域名（如 `eatwhat.cloud`）
- `POSTGRES_PASSWORD`：数据库强密码
- `JWT_SECRET`：JWT 强随机密钥
- `DASHSCOPE_API_KEY`/`OPENAI_API_KEY` 等（按你选择的 LLM provider）

重要：为了避免浏览器在 HTTPS 页面里请求 `http://localhost:8000` 被拦截，**单域名方案必须设置**：

```env
APP_API_BASE_URL=https://<DOMAIN>
```

---

## 5. 一键部署（推荐）

本仓库提供开箱即用脚本：

- `deploy/deploy.sh`：构建并启动生产服务
- `deploy/enable_https.sh`：申请证书并开启 HTTPS
- `deploy/renew_https.sh`：续期证书并 reload nginx

### 5.1 首次部署（HTTP 或临时端口）

```bash
cd ~/code/smart-eats-ai-bakend
./deploy/deploy.sh
```

此时（未启用 gateway 时）前端默认会暴露：

- `http://<SERVER_IP>:${FRONTEND_HOST_PORT}`（默认 80）

你也可以临时设置：

```env
FRONTEND_HOST_PORT=8080
```

然后访问：`http://<SERVER_IP>:8080/`

### 5.2 开启 HTTPS（单域名）

确保：

- DNS 已生效（`eatwhat.cloud` 指向服务器）
- 80/443 已放通

然后运行：

```bash
cd ~/code/smart-eats-ai-bakend
./deploy/enable_https.sh
```

启用后访问：

- `https://<DOMAIN>/`

---

## 6. 手动部署（不使用脚本也能照做）

> 下面给的是“手动命令版”，脚本内部做的就是这些。

### 6.1 启动生产服务（不含 gateway）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

检查：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml ps

docker logs --tail 100 smart-eats-backend
curl -sS http://127.0.0.1:8000/ | head
```

### 6.2 启动 gateway（HTTP，仅用于签证书）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.https.yml up -d gateway
```

从公网验证：

- `http://<DOMAIN>/` 能打开（或能返回 HTML）

### 6.3 申请证书（Let’s Encrypt）

```bash
docker compose --env-file .env.prod \
  -f docker-compose.prod.yml -f docker-compose.https.yml \
  --profile tools run --rm certbot \
  certonly --webroot -w /var/www/certbot \
  -d <DOMAIN> \
  --agree-tos --register-unsafely-without-email --non-interactive
```

### 6.4 切换 gateway 到 HTTPS

把 `deploy/nginx/gateway.conf` 切到 HTTPS 配置（脚本会自动生成）。然后：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.https.yml up -d --force-recreate gateway
```

验证：

```bash
curl -I http://<DOMAIN>/ | head -n 5
curl -I https://<DOMAIN>/ | head -n 5
curl -I https://<DOMAIN>/docs | head -n 5
```

---

## 7. 常用运维

```bash
cd ~/code/smart-eats-ai-bakend

# 查看状态
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep smart-eats

# 查看日志
docker logs -f --tail 200 smart-eats-backend
docker logs -f --tail 200 smart-eats-frontend
docker logs -f --tail 200 smart-eats-gateway

# 重新部署（代码更新后）
./deploy/deploy.sh

# 续期证书
./deploy/renew_https.sh
```

---

## 8. 常见问题

### 8.1 注册/登录“立刻失败”

通常是前端 API Base URL 指向了 `http://localhost:8000`（或 http 协议），在 https 页面中会被浏览器拦截。

确保 `.env.prod` 里：

```env
APP_API_BASE_URL=https://<DOMAIN>
```

然后重建前端：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml build --no-cache frontend
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d frontend
```

并用无痕/强刷访问页面。

### 8.2 gateway 起不来

```bash
docker logs --tail 200 smart-eats-gateway
```

常见原因：

- `server_name` 写法错误或为空
- 证书路径不存在（证书未签发就上 HTTPS 配置）
- upstream 服务名写错（应为 `frontend` / `backend`）

---

## 9. 关键文件说明

- `docker-compose.prod.yml`：生产服务（postgres/redis/backend/frontend）
- `docker-compose.https.yml`：HTTPS 叠加层（gateway/certbot），并禁用 frontend 端口映射
- `deploy/nginx/gateway.conf`：Nginx 网关配置（脚本生成/可修改）
- `.env.prod`：生产环境变量（你自己的，不要提交 git）
- `.env.prod.example`：生产环境变量模板（开箱即用的默认模板）

