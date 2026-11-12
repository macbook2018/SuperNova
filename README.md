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

- GET /v1/models：返回当前接口密钥所属用户可用的人格列表。
- POST /v1/chat/completions：根据请求中的 model 选择人格并执行对话。
- stream=true：启用 SSE 流式响应，结束标记为 data: [DONE]。

认证使用标准请求头：

```http
Authorization: Bearer <API_KEY>
```

接口密钥绑定用户及其租户。服务端不得信任客户端自行传入的 tenant_id 或 user_id。访问人格、知识库、会话和文件前，必须再次校验资源归属。

## 六、开发阶段

0. **工程基础（已完成）**：仓库规范、本地环境、数据库迁移、健康检查、结构化日志和自动化测试。
1. **中文用户后台原型（已完成）**：工作台、人格、知识库、API、用量和账户设置页面；Phase 3 将页面逐步接入真实 API。
2. **身份与租户管理（已完成开发）**：用户注册、登录、JWT 会话、租户上下文、用户级 API Key 及跨租户权限边界。
3. **人格与模型管理（当前开发阶段）**：人格 CRUD、模型供应商配置、凭据加密保存、知识库元数据和人格关联。
4. **统一接口网关**：兼容 OpenAI 的模型列表、对话接口、流式响应和用量记录。
5. **知识库**：文档和链接导入、解析、切片、向量化、检索及租户隔离。
6. **AstrBot 运行管理**：Worker 隔离部署、生命周期、适配器集成、健康监控和故障恢复。
7. **渠道与运营**：微信渠道、会话管理、用量限制、套餐计费、管理后台和生产环境加固。

每个阶段都必须提供自动化测试和验收报告。仅编译通过不能视为阶段完成。

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

- Phase 0 工程基础已通过 GitHub Actions 验收。
- Phase 1 中文用户后台交互原型已完成。
- Phase 2 用户身份、租户上下文和用户级 API Key 已完成开发，已提供注册、登录、刷新会话和密钥生命周期接口。
- Phase 3 正在接入人格 CRUD、模型供应商加密配置、知识库元数据及多对多关联；前端已增加真实登录与目录 API。
- 统一对话网关、模型真实调用、知识库文档处理、Token 计量和 AstrBot Worker 尚未完成。

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

API Key 只绑定 user_id 和 tenant_id，不绑定人格。明文只在创建或轮换时展示一次，数据库只保存 SHA-256 摘要。此阶段尚未开放 API Key 对话认证，后续统一网关阶段完成。

## 十二、人格、供应商与知识库目录接口

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


## 十一、许可证

正式公开发布前确定许可证及分发条款。
