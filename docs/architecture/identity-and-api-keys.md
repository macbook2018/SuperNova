# 身份认证、租户上下文与用户级 API Key

## 身份与工作空间

- 注册创建一个 User、一个 Tenant 和一条 owner Membership。
- 用户邮箱在全平台规范化为小写并保持唯一。
- User 与 Tenant 分离；TenantMembership 表示用户在工作空间中的角色。
- 当前首期登录选择用户最早创建的有效 Membership；工作空间切换在后续成员与多工作空间功能中实现。
- 业务请求的 tenant_id 只能来自已验证的 JWT 或 API Key，不接受客户端自行指定。

## 认证接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | /auth/register | 注册用户并创建默认工作空间 |
| POST | /auth/login | 邮箱密码登录 |
| POST | /auth/refresh | 轮换 Refresh Token |
| POST | /auth/logout | 撤销当前 Refresh Token |
| GET | /auth/me | 获取当前用户与工作空间 |

Access Token 使用 HS256 签名，默认有效期 15 分钟。Refresh Token 为高熵随机不透明令牌，只保存 SHA-256 摘要，有效期默认 30 天。每次刷新都会撤销旧令牌并签发新令牌；检测到已撤销令牌被重放时，撤销该令牌家族的所有活动会话。Access Token 在自然到期前不会被即时撤销，因此登出后的最长残余有效期为 Access Token TTL。

密码使用 Argon2id 哈希，不保存明文密码。邮箱验证、密码找回、登录风控和分布式速率限制尚未接入，不得将当前注册入口直接暴露为无防护的公网生产注册服务。

## 用户级 API Key

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | /api-keys | 创建用户级 API Key |
| GET | /api-keys | 列出当前用户在当前工作空间的密钥元数据 |
| POST | /api-keys/{key_id}/rotate | 原子撤销旧密钥并创建新密钥 |
| DELETE | /api-keys/{key_id} | 撤销密钥 |

- 密钥格式为 sn_live_ 前缀加高熵随机值。
- Key 绑定 user_id 和 tenant_id，不绑定 persona_id / bot_id。
- 当前用户的所有人格将来共用此用户级密钥；请求 model 字段用于选择人格。
- 数据库只保存 SHA-256 摘要和展示前缀；明文只在创建或轮换响应中返回一次。
- 列表接口不返回摘要或明文；撤销和轮换必须同时匹配 key_id、user_id、tenant_id。
- 当前阶段只完成密钥生命周期管理，尚未开放 API Key 对话认证；统一网关阶段再验证摘要、更新 last_used_at 并路由 model。

## 数据隔离约束

1. Access Token 包含 sub、tid、typ、iss、iat、exp、jti。
2. 身份依赖重新验证用户、工作空间和 Membership 均为有效状态。
3. 业务服务只使用 Principal 中的 user、tenant 和 membership。
4. API Key 查询同时约束 user_id 和 tenant_id；跨用户访问统一返回 404。
5. Refresh Session 绑定 user_id 和 tenant_id；轮换时重新验证用户和 Membership。

## 本地开发配置

- SUPERNOVA_JWT_SECRET：开发环境提供本地默认值；生产环境必须配置唯一且至少 32 字符的随机密钥。
- SUPERNOVA_ACCESS_TOKEN_TTL_MINUTES：5 到 60 分钟，默认 15。
- SUPERNOVA_REFRESH_TOKEN_TTL_DAYS：1 到 90 天，默认 30。
- SUPERNOVA_CORS_ORIGINS：逗号分隔的前端源白名单。

## 生产上线前的安全门槛

- 邮箱验证、密码找回与注册滥用防护。
- 登录、注册、刷新和 API Key 创建的速率限制。
- Access Token 登出即时撤销策略（如需要）。
- JWT 密钥轮换与密钥管理服务。
- HTTPS、Cookie / 前端 Token 保存策略和 CSP。
- 安全审计、异常登录告警与账户冻结流程。
