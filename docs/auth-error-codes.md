# Auth Error Codes (410xx)

## 41001 AUTH_OTP_INVALID
OTP 无效（格式错误/校验失败）。

## 41002 AUTH_OTP_EXPIRED
OTP 已过期。（保留位，逐步替换细分场景）

## 41003 AUTH_ACCOUNT_LOCKED
账号临时锁定（暴力破解防护触发）。

## 41004 AUTH_TOKEN_REPLAY_DETECTED
检测到 Refresh Token 重放攻击。

## 41005 AUTH_SESSION_REVOKED
会话已撤销或 refresh token 不可用。

## 41006 AUTH_RECENT_AUTH_REQUIRED
需要近期二次认证。（预留位）

## 41007 AUTH_ACCOUNT_EXISTS
注册/绑定时账号已存在。

## 41008 AUTH_ACCOUNT_REQUIRED
缺少 account/email/phone 等必要身份字段。

## 41009 AUTH_INVALID_CREDENTIALS
账号或密码错误（统一隐晦返回）。

## 41010 AUTH_RESET_CODE_INVALID
重置密码验证码无效或过期。

## 41011 AUTH_OAUTH_BIND_CONFLICT
第三方账号已绑定到其他用户。

## 41012 AUTH_OAUTH_PROVIDER_UNSUPPORTED
OAuth/通道提供方未支持或未配置。
