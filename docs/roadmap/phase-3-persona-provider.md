# Phase 3：人格、模型供应商与知识库元数据

- 状态：开发完成，GitHub Actions 全部通过，待依赖 PR 合并与本机验收
- 基础分支：feat/phase-2-identity-tenant
- 实现分支：feat/phase-3-persona-provider

## 目标

把中文控制台从演示数据切换为真实业务 API。用户登录后，可以在自己的工作空间中创建人格、配置模型供应商、保存知识库元数据并建立人格与知识库的多对多关联。

## 已实现

- [x] ModelProvider、Persona、KnowledgeBase、PersonaKnowledgeBase 数据模型。
- [x] Alembic 迁移 0003_persona_provider_catalog。
- [x] 模型供应商 CRUD：创建、列表、编辑、启停和删除。
- [x] 供应商类型：OpenAI 兼容、OpenAI、DeepSeek、通义千问、Anthropic。
- [x] 供应商 API Key 使用 Fernet 加密保存，接口只返回末四位提示。
- [x] 人格 CRUD：创建、列表、详情、编辑、删除和启停。
- [x] 人格独立保存 system prompt、provider、model name、temperature、top_p 和 max_tokens。
- [x] 知识库元数据 CRUD。
- [x] 人格与知识库多对多关联，服务层和数据库复合外键均限制为同一 tenant。
- [x] Vue 登录/注册页面、JWT 会话保存、自动刷新和退出登录。
- [x] 前端人格、模型供应商、知识库和 API Key 页面接入真实后端。
- [x] 删除控制台中用于冒充真实业务结果的示例人格、示例供应商、示例文档、演示 API Key 和模拟用量。
- [x] GitHub Actions 全量验收通过。
- [ ] 合并依赖 PR 后进行本机注册、登录、供应商配置和人格关联验收。

## API

### 模型供应商

- GET /model-providers
- POST /model-providers
- PATCH /model-providers/{provider_id}
- DELETE /model-providers/{provider_id}

### 人格

- GET /personas
- POST /personas
- GET /personas/{persona_id}
- PATCH /personas/{persona_id}
- DELETE /personas/{persona_id}

### 知识库元数据

- GET /knowledge-bases
- POST /knowledge-bases
- PATCH /knowledge-bases/{knowledge_base_id}
- DELETE /knowledge-bases/{knowledge_base_id}

全部接口要求 Bearer JWT。服务端从已验证 Principal 获取 tenant_id；客户端不能自行声明 tenant_id。

## 数据隔离与凭据安全

- model_providers、personas、knowledge_bases 都包含 tenant_id。
- Persona 对 ModelProvider 使用 (tenant_id, provider_id) 复合外键。
- PersonaKnowledgeBase 使用 tenant_id 与资源 ID 的复合外键，数据库层也禁止跨租户关联。
- 删除仍被人格使用的供应商返回 409；必须先修改人格。
- API Key 明文不返回；数据库只保存 Fernet 密文和末四位提示。
- 生产环境必须配置独立的 SUPERNOVA_ENCRYPTION_KEY，禁止使用开发默认值。
- 更换加密主密钥前必须对现有供应商密文执行解密/重加密迁移；不能直接覆盖环境变量。

## 当前边界

- 模型供应商配置已保存，但尚未执行真实供应商连通性测试。
- 尚未实现模型列表发现、真实模型调用、流式响应和模型供应商错误归一化。
- 知识库目前只有元数据；文档上传、解析、切片、Embedding、pgvector 检索属于后续知识库阶段。
- Token 计量、套餐、扣费和支付尚未接入。
- API Key 已实现创建、列表、轮换和撤销；尚未用于真实对话网关认证。
- 登录页已接入邮箱密码注册/登录，但邮箱验证、找回密码、注册风控和速率限制仍是公网开放前的安全门槛。
- 浏览器当前使用 localStorage 保存 Access Token 与 Refresh Token；生产环境应结合 CSP、XSS 防护和更严格的会话存储策略评估 HttpOnly Cookie 方案。

## 验收标准

1. 用户 A 无法读取、修改、删除用户 B 的供应商、人格或知识库。
2. 用户 A 无法把用户 B 的 provider_id 或 knowledge_base_id 关联到自己的人格。
3. 供应商 API Key 在数据库中为 Fernet 密文，任何 API 响应、日志和错误均不包含明文。
4. 人格 slug 在同一 tenant 内唯一，不同 tenant 可使用相同 slug。
5. 人格创建/更新能完整保存模型参数和知识库关联。
6. 删除供应商前检查引用；删除知识库时清理关联关系。
7. Alembic 从空数据库升级成功；Ruff、pytest、控制台生产构建和 Docker Compose 冒烟检查全部通过。

## 自动化验收结果

- GitHub Actions run #115：全部 jobs success。
- Ruff：通过。
- pytest：27 项通过。
- Alembic：从空 PostgreSQL 数据库升级至 head 成功。
- Docker Compose：构建、数据库迁移、API 健康检查、Silo 存储就绪和中文控制台 HTTP 冒烟检查通过。
- Vue TypeScript 类型检查和 Vite 生产构建通过。
- 运行记录：https://github.com/macbook2018/SuperNova/actions/runs/43148639762

用户本机尚未执行真实注册、登录、模型供应商凭据保存和人格/知识库关联验收。请在依赖 PR 合并后完成一次端到端手工验收。
