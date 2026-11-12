# Phase 2：用户身份、租户上下文与用户级 API Key

- 状态：实现完成，待依赖 PR 合并与用户本机验收
- 基础分支：feat/phase-1-console-prototype
- 实现分支：feat/phase-2-identity-tenant

## 目标

建立可靠的身份与租户边界，为人格、知识库、渠道和统一 API 网关提供可信的用户上下文。所有业务资源必须在此阶段之后再开放。

## 交付内容

- [x] User、Tenant、TenantMembership 数据模型与 Alembic 迁移。
- [x] 邮箱注册、Argon2id 密码哈希和密码登录。
- [x] 短时 JWT Access Token。
- [x] 不透明 Refresh Token 哈希存储、单次轮换和重放检测。
- [x] /auth/me 当前身份与工作空间接口。
- [x] 用户级 API Key 创建、列表、轮换和撤销。
- [x] API Key 绑定 user_id + tenant_id，不绑定人格。
- [x] API Key 明文只在创建或轮换时返回一次，数据库只保存摘要。
- [x] 当前用户与工作空间范围约束及跨用户负向测试。
- [x] 前端开发源 CORS 白名单与生产 JWT Secret 配置校验。
- [x] CI Ruff、pytest、Alembic 和 Docker Compose 全量验收。
- [x] 自动化测试与阶段验收记录。

## API

- POST /auth/register
- POST /auth/login
- POST /auth/refresh
- POST /auth/logout
- GET /auth/me
- POST /api-keys
- GET /api-keys
- POST /api-keys/{key_id}/rotate
- DELETE /api-keys/{key_id}

## 验收标准

1. 注册自动创建用户、默认工作空间及 owner Membership。
2. 密码以 Argon2id 哈希存储，任何用户接口都不返回 password_hash。
3. 登录错误不泄露邮箱是否存在。
4. Refresh Token 每次使用后立即轮换；旧令牌重放会撤销同一令牌家族。
5. 用户 A 无法列出、轮换或撤销用户 B 的 API Key。
6. API Key 数据库不保存明文；列表响应不返回明文或摘要。
7. JWT 伪造、过期、错误类型或已失效用户均无法访问受保护接口。
8. 所有测试通过；Alembic 从空数据库升级成功；Docker Compose 健康检查通过。

## 明确不在本阶段

- 人格与模型供应商 CRUD。
- OpenAI 兼容对话网关和 API Key 对话认证。
- 知识库上传、解析和向量检索。
- AstrBot Worker 的创建与运行。
- 真实 Token 计量、套餐和支付。
- 邮箱验证、密码找回、注册风控和登录速率限制。这些属于公网注册开放前的安全上线门槛。

## 自动化验收结果

- GitHub Actions run #69：全部 jobs success。
- Ruff：通过。
- pytest：18 项通过。
- Alembic：从空 PostgreSQL 数据库升级至 head 成功。
- Docker Compose：配置校验、镜像构建、数据库迁移、API 健康检查、Silo 就绪检查和中文控制台 HTTP 冒烟检查全部通过。
- 控制台 TypeScript 类型检查与 Vite 生产构建通过。
- 本地 macOS 环境尚未执行 Phase 2 注册/登录与 API Key 实测；需在依赖 PR 合并后由用户本机验收。

自动化运行记录：https://github.com/macbook2018/SuperNova/actions/runs/43144496494
