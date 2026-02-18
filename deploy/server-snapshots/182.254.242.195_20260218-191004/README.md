# Server snapshot (config/runtime)

- server: `dev@182.254.242.195`
- path: `/home/dev/code/smart-eats-ai-bakend`
- capturedAt: `20260218-191004` (Asia/Shanghai)

This snapshot records **server-side config changes** made during deployment/debugging so local development won't miss them.

Notes:
- `.env.prod` is **redacted** (secrets removed).
- The canonical, reusable templates live in:
  - `.env.prod.example`
  - `deploy/nginx/gateway.*.template.conf`
  - `deploy/enable_https.sh`

If the server is re-provisioned, follow `deploy/DEPLOYMENT_GUIDE.md` and use this snapshot as reference.
