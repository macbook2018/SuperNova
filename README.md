# SuperNova

> 基于 AstrBot 的多租户 AI Agent SaaS 平台。

SuperNova 让每位用户创建多个独立 AI 人格，配置模型与知识库，并通过微信渠道或 OpenAI 兼容 API 接入业务系统。

## 产品原则

- **租户隔离优先**：用户、人格、模型凭据、知识库、会话、用量记录和运行时资源必须具备明确的租户边界。
- **AstrBot 负责 Agent 运行时**：优先通过适配器和集成边界扩展，避免不必要地 Fork 或侵入上游核心。
- **一个用户级 API Key，多个人格**：API Key + Base URL 用于识别用户，OpenAI 兼容请求中的 `model` 用于选择该用户有权访问的人格。
- **OpenAI 兼容**：支持 `GET /v1/models`、`POST /v1/chat/completions` 和 SSE 流式响应。
- **可观测、可计量**：记录 Request ID、Token 用量、延迟、错误和用户级消耗。
- **品牌一致性**：使用已确认的 SuperNova S 形轨道 Logo，以及深空蓝、青绿、蓝紫渐变视觉体系。

## 技术基线

| 层级 | 技术 |
| --- | --- |
| Web 控制台 | Vue 3 + TypeScript |
| 控制平面 API | Python + FastAPI |
| 主数据库 | PostgreSQL |
| 向量检索 | pgvector |
| 缓存 / 队列 | Redis |
| 对象存储 | MinIO |
| Agent 运行时 | 隔离的 AstrBot Worker |
| 部署 | 开发环境 Docker Compose，生产环境容器化部署 |

控制平面负责用户、租户、API Key、人格、模型供应商配置、知识库、渠道配置、会话、用量和 Worker 生命周期。AstrBot Worker 执行 Agent 对话。统一模型网关负责身份识别、人格解析、模型路由、流式输出和用量计量。

## 本地开发

需要 Docker Desktop（含 Docker Compose v2）、Python 3.12+。

```bash
cp .env.example .env
make dev-up
make dev-migrate
```

服务启动后：

- API：<http://localhost:8000>
- OpenAPI：<http://localhost:8000/docs>
- Liveness：<http://localhost:8000/health/live>
- Readiness：<http://localhost:8000/health/ready>
- MinIO Console：<http://localhost:9001>

常用命令：

```bash
make dev-logs
make dev-down
make api-install
make api-lint
make api-test
```

`.env.example` 中的凭据仅供本机开发。部署到共享或生产环境前必须替换全部默认密码；不要提交真实凭据。PostgreSQL 和 Redis 的本地端口仅绑定到 `127.0.0.1`。

## 统一 API 约定

- `GET /v1/models`：返回当前 API Key 对应用户可用的人格列表。
- `POST /v1/chat/completions`：通过 `model` 解析人格，并执行对话。
- `stream=true`：返回 OpenAI 兼容 SSE，结束时发送 `data: [DONE]`。

认证使用标准请求头 `Authorization: Bearer <API_KEY>`。API Key 绑定用户及其租户；服务端不得信任请求体或 URL 中由客户端自行传入的 tenant_id。任何人格、知识库或会话访问都必须再次校验资源归属。

## 交付顺序

1. **工程基础**：仓库规范、本地开发环境、配置、数据库迁移、健康检查、结构化日志和 CI。
2. **控制平面**：用户认证、租户上下文、用户级 API Key 生命周期和权限边界。
3. **人格与模型管理**：人格 CRUD、模型供应商配置、密钥安全存储和模型选择。
4. **统一 API 网关**：OpenAI 兼容 models/chat-completions、SSE、请求追踪和用量记录。
5. **知识库**：文档/链接导入、解析、切片、Embedding、检索和租户级隔离。
6. **AstrBot 运行时编排**：Worker 隔离部署、生命周期、适配器集成、健康监控和故障恢复。
7. **渠道与运营**：微信渠道、会话管理、用量限制、计费接口、管理控制台和生产加固。

每个阶段都必须有自动化测试和验收报告；仅编译通过不能视为阶段完成。

## 仓库结构

```text
apps/
  api/                 FastAPI 控制平面与统一 API 网关
    app/               API 应用代码
    migrations/        Alembic 数据库迁移
  console/             Vue 3 管理控制台（后续阶段）
  worker-manager/      AstrBot Worker 生命周期与适配边界（后续阶段）
packages/
  contracts/           共享 API Schema 与客户端契约（后续阶段）
  sdk/                 OpenAI 兼容 SDK 示例与辅助工具（后续阶段）
deploy/
  docker/              本地和生产容器定义
  scripts/             开发及发布脚本（后续阶段）
docs/
  architecture/        系统设计与租户隔离模型
  decisions/           架构决策记录
  roadmap/             里程碑与验收标准
```

## 安全要求

- API Key 仅存储不可逆哈希；创建时明文只展示一次。
- 模型供应商密钥必须加密存储，读取 API 不得返回密钥明文。
- 租户身份必须从已认证的 API Key / 登录会话推导，不接受客户端自报身份。
- 服务层和数据访问层都必须执行租户范围校验，并提供跨租户负向测试。
- 浏览器不得接触 AstrBot Worker 凭据、内部地址或模型供应商密钥。
- 密钥不得进入 Git、日志、链路追踪或错误响应。

## 当前状态

Phase 0 正在完成工程基础验收。当前已包含 FastAPI 可运行骨架、健康检查、Request ID、结构化日志、Docker Compose 开发依赖、Alembic 迁移框架和自动化测试。下一阶段必须先通过 Phase 0 验收，再开始用户认证与租户上下文。

## License

正式公开发布前确定许可证及分发条款。
