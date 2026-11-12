# SuperNova

> 基于 AstrBot 构建的多租户智能体服务平台。

SuperNova 允许每位用户创建多个相互独立的 AI 人格，分别配置提示词、模型、知识库和渠道，并通过微信或兼容 OpenAI 标准的接口调用自己的机器人。

## 一、产品语言

- 平台默认语言：**简体中文（zh-CN）**。
- 用户后台、管理后台、表单、菜单、按钮、通知、错误提示、帮助文档和账单页面均使用简体中文。
- 后续如增加英文界面，必须通过独立语言包提供，不得在同一页面中随意中英混排。
- API 路径、JSON 字段、数据库字段、代码变量、环境变量和第三方协议名称保留标准英文，避免破坏程序兼容性。
- 第三方基础设施自带的管理控制台不属于 SuperNova 产品界面，其语言由对应第三方项目决定。

详细要求见《产品语言与界面规范》（docs/产品语言规范.md）。

## 二、产品原则

- **数据隔离优先**：用户、人格、模型凭据、知识库、会话、用量记录和运行资源必须具备明确的租户边界。
- **AstrBot 负责智能体运行**：优先通过适配器和集成边界扩展，避免不必要地修改上游核心。
- **一个用户级接口密钥，多个独立人格**：接口密钥与用户绑定；请求中的 model 字段用于选择该用户有权使用的人格。
- **兼容 OpenAI 接口**：提供 GET /v1/models、POST /v1/chat/completions 和 SSE 流式响应。
- **默认平台模型、允许 BYOK**：新建人格默认使用 SuperNova 提供的模型，可选用户自带 API Key；原有人格保留 BYOK 配置。
- **订阅 + 超额资源收费**：BYOK 用户的模型 Token 不收取 SuperNova 模型费；平台资源超额必须明示并经用户授权。
- **用量可追踪**：记录请求编号、Token 用量、响应时间、错误和用户消耗。
- **安全优先**：所有数据访问、文件存储、向量检索和机器人运行资源都必须执行租户隔离。

## 三、技术架构

| 层级 | 技术 |
| --- | --- |
| 用户控制台 | Vue 3 + TypeScript |
| 服务端接口 | Python + FastAPI |
| 主数据库 | PostgreSQL |
| 向量检索 | pgvector |
| 缓存与任务队列 | Redis |
| 对象存储 | S3 兼容存储（开发环境使用 Silo） |
| 智能体运行时 | 按租户隔离的 AstrBot Worker |
| 部署 | Docker Compose 开发环境、容器化生产部署 |

控制平面负责用户、租户、接口密钥、人格、模型供应商、知识库、渠道、会话、用量和 Worker 生命周期。AstrBot Worker 负责执行智能体对话。统一模型网关负责身份识别、人格解析、模型路由、流式输出和用量计量。

开发环境使用 PGSTY Silo 作为 S3 兼容对象存储。Silo 是社区维护的 MinIO 兼容分支，并非 MinIO 官方发行版。应用层只依赖标准 S3 接口，以便未来替换对象存储服务。

## 四、本地开发

需要安装 Docker Desktop（包含 Docker Compose v2）和 Python 3.12 或更高版本。

在终端中进入 SuperNova 项目目录，然后执行：

```bash
cp .env.example .env
make dev-up
make dev-migrate
```

服务启动后：

| 服务 | 地址 |
| --- | --- |
| SuperNova 接口 | http://localhost:8001 |
| 接口文档 | http://localhost:8001/docs |
| 中文用户后台 | http://localhost:5173 |
| 存活检查 | http://localhost:8001/health/live |
| 就绪检查 | http://localhost:8001/health/ready |
| 对象存储管理控制台 | http://localhost:9001（右上角语言菜单可选“简体中文”） |

常用命令：

```bash
make dev-logs       # 查看服务日志
make console-build   # 构建中文用户后台
make dev-down       # 停止开发环境
make api-install    # 安装接口服务开发依赖
make api-lint       # 执行代码规范检查
make api-test       # 运行自动化测试
make dev-migrate    # 执行数据库迁移
```

注意：.env.example 中的密码仅供本机开发使用。部署到共享或生产环境前必须更换所有默认密码，禁止将真实密钥提交到 Git。PostgreSQL 和 Redis 的开发端口仅绑定到本机地址。

## 五、统一接口约定

- GET /v1/models：返回当前 API Key 所属用户可调用的人格列表，模型 ID 为人格 slug。
- POST /v1/chat/completions：根据请求中的 model 人格 slug 路由到人格配置的模型供应商。
- stream=true：启用 SSE 流式响应，结束标记为 data: [DONE]。
- GET /usage/summary 与 GET /usage/records：登录用户查看真实 Token 用量与调用记录。

认证使用标准请求头：

```http
Authorization: Bearer <API_KEY>
```

接口密钥绑定用户及其租户。服务端不得信任客户端自行传入的 tenant_id 或 user_id。访问人格、知识库、会话和文件前，必须再次校验资源归属。

## 六、开发阶段

0. **Phase 0：工程基础（已开发）**：环境、数据库迁移、健康检查、结构化日志。
1. **Phase 1：中文用户后台原型（已开发）**：工作台、人格、知识库、API、用量和账户设置。
2. **Phase 2：身份与租户管理（已开发）**：注册/登录、JWT、用户级 API Key 与租户隔离。
3. **Phase 3：人格与模型管理（已开发）**：人格 CRUD、BYOK 模型供应商加密凭据、知识库元数据与人格关联。
4. **Phase 4：统一 API 网关与 Token 计量（已开发，已完成真实 DeepSeek 调用与后台用量验收）**。
5. **Phase 5：混合模型来源、套餐、计费和超额资源（当前阶段）**：
   - 5A：ADR、非破坏性 schema / ORM、Decimal 报价基础，默认不扣费。
   - 5B：平台模型池、人格来源切换、订阅权益与预算预留。
   - 5C：幂等用量、影子计价、资金账本及对账。
   - 5D：支付、退款、账单及授权后真实收费。
6. **Phase 6：知识库完整功能**：上传/链接导入、解析、切片、向量检索和知识库资源计量。
7. **Phase 7：AstrBot 多租户运行管理**：Worker 隔离与机器人生命周期、运行资源计量。
8. **Phase 8：渠道、运营和商用部署**：微信渠道产品化、后台运营、风控与生产监控。

**不得将 schema 和 Token 用量等同于正式扣费已上线。** Phase 5A 仅迁移数据库和引入无副作用的定价纯函数。待幂等、预算预留、退款和支付回调验收后才可能开启真实收费。

详细设计参见 [ADR-0004](docs/decisions/0004-hybrid-billing-byok-platform.md)、[计费与权益架构](docs/architecture/billing-and-entitlements.md) 和 [Phase 5–8 路线图](docs/roadmap/phase-5-8-commercial-rollout.md)。

## 七、仓库结构

```text
apps/
  api/                 服务端控制平面与统一接口网关
  console/             Vue 3 + TypeScript 中文用户后台
  worker-manager/      AstrBot 运行实例管理（后续阶段）
packages/
  contracts/           共享接口数据结构（后续阶段）
  sdk/                 OpenAI 兼容接口示例与工具包（后续阶段）
deploy/
  docker/              本地与生产容器配置
  scripts/             开发与发布脚本
docs/
  architecture/        系统架构与数据隔离设计
  decisions/           架构决策记录
  roadmap/              阶段计划与验收标准
```

## 八、安全要求

- 用户接口密钥只保存不可逆哈希，创建时仅展示一次明文。
- 模型供应商密钥必须加密保存，读取接口不得返回密钥明文。
- 租户身份必须从已验证的接口密钥或登录会话中取得，不接受客户端自行声明身份。
- 服务层和数据访问层都必须校验租户范围，并覆盖跨租户越权测试。
- 浏览器不得接触 AstrBot Worker 凭据、内部地址或模型供应商密钥。
- 密钥不得写入 Git、日志、链路追踪或错误响应。

## 九、当前进度

- Phase 0–3 基础工程、注册、租户、人格和模型供应商管理已开发。
- Phase 4 API Key → 人格 slug → DeepSeek 实际对话调用及真实 Token 用量统计已由用户在本地验证成功；PR 依赖仍需依序合并。
- **Phase 5A 已提交混合模式架构、套餐与账务 schema、非破坏性 Alembic 0005 迁移和 Decimal 定价基础；自动化验收状态见对应 PR。**
- 平台模型代理、来源切换 UI、钱包扣款、支付、退款、知识库文档处理和 AstrBot Worker 尚未上线。
- BYOK 模型 Token 继续只做使用量统计，SuperNova 模型收费为 0。

## 十、身份与密钥接口

身份接口使用 Bearer JWT；Access Token 默认 15 分钟，Refresh Token 默认 30 天并在每次刷新时轮换。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | /auth/register | 注册用户并创建默认工作空间 |
| POST | /auth/login | 邮箱密码登录 |
| POST | /auth/refresh | 轮换 Refresh Token |
| POST | /auth/logout | 撤销 Refresh Token |
| GET | /auth/me | 获取当前用户与工作空间 |
| POST | /api-keys | 创建用户级 API Key |
| GET | /api-keys | 列出当前用户的密钥元数据 |
| POST | /api-keys/{key_id}/rotate | 轮换用户级 API Key |
| DELETE | /api-keys/{key_id} | 撤销用户级 API Key |

API Key 只绑定 user_id 和 tenant_id，不绑定人格。明文只在创建或轮换时展示一次，数据库只保存 SHA-256 摘要。统一网关通过 Bearer API Key 认证；同一用户级 Key 可调用当前工作空间内多个已启用人格。

## 十一、人格、供应商与知识库目录接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET / POST | /model-providers | 列出或创建当前工作空间的模型供应商 |
| PATCH / DELETE | /model-providers/{provider_id} | 编辑或删除供应商 |
| GET / POST | /personas | 列出或创建人格 |
| GET / PATCH / DELETE | /personas/{persona_id} | 查询、编辑或删除人格 |
| GET / POST | /knowledge-bases | 列出或创建知识库元数据 |
| PATCH / DELETE | /knowledge-bases/{knowledge_base_id} | 编辑或删除知识库元数据 |

人格可配置 system_prompt、provider_id、model_name、temperature、top_p、max_tokens 和 knowledge_base_ids。知识库与人格为同租户多对多关联。当前知识库尚未接入文档上传和向量检索，模型供应商尚未执行真实连通性测试或推理调用。

供应商 API Key 使用 Fernet 密文保存，读取接口仅返回密钥末四位提示。生产环境必须配置独立 Fernet key；更换密钥前必须迁移重加密现有供应商凭据。


## 十二、许可证

正式公开发布前确定许可证及分发条款。
