# Phase 0：工程基础与可运行骨架

- 状态：Complete
- 验收日期：2026-11-11
- CI：GitHub Actions run #6（全部 jobs success）
- 目标：建立可重复启动、可测试、可扩展的仓库基础，不提前堆叠未验证的业务模块。

## 已交付

- 项目 README、产品原则、技术基线和交付顺序。
- 控制平面 / 运行平面架构说明。
- 多租户隔离、安全边界和三项架构决策。
- FastAPI 最小服务、Request ID 中间件和健康检查端点。
- PostgreSQL + pgvector、Redis、Silo（MinIO 兼容 S3）的 Docker Compose 开发环境。
- Alembic 迁移框架与 pgvector 扩展初始化迁移。
- 结构化日志、非 root API 容器、开发命令和 Docker 构建上下文排除规则。
- API 健康检查、Request ID、404/500 错误响应和依赖就绪状态测试。
- GitHub Actions lint、测试、迁移及 Compose 冒烟验收工作流。

## 验收结果

### 工程与运行

- [x] `docker compose -f deploy/docker/compose.yaml config --quiet` 配置校验通过。
- [x] `docker compose -f deploy/docker/compose.yaml up --build -d` 在 CI 环境成功启动。
- [x] API `/health/live` 返回 200。
- [x] API `/health/ready` 在 PostgreSQL、Redis 正常时返回 200。
- [x] 依赖不可用时 Readiness 返回 503（自动化测试）。
- [x] 当前 API 成功、404 和 500 响应均验证 X-Request-ID；错误响应不泄露内部异常详情。
- [x] API 容器运行用户 UID 非 0。
- [x] Silo S3 存储就绪探针通过。

### 质量

- [x] Ruff 检查通过。
- [x] pytest 全部通过。
- [x] Alembic pgvector 初始迁移成功。
- [x] GitHub Actions CI 全部 jobs 通过。
- [x] `.env`、真实密钥文件和本地数据目录已加入忽略规则；仓库不包含本地数据卷。
- [x] 提供启动、停止、日志、迁移和测试命令。

## 验收范围说明

自动验收运行于 GitHub Actions Ubuntu 环境，覆盖 API、PostgreSQL、Redis、Silo 和 Docker Compose。用户本机 macOS / Docker Desktop 的首次启动尚未执行，不将 CI 结果表述为本机验收结果。

开发环境对象存储使用 PGSTY Silo 固定版本。Silo 是社区维护的 MinIO 兼容分支，并非 MinIO, Inc. 官方发行版；选择原因、兼容边界和生产评估要求见 ADR 0003。

## 本阶段明确不做

- 不重复验证已完成的微信收发 PoC。
- 不修改 AstrBot 上游核心。
- 不在缺少租户身份与权限边界的情况下先写人格、知识库或会话业务 API。
- 不将本地 Compose 通过等同于生产环境就绪。

## 下一阶段入口

Phase 1 中文用户后台交互原型已单独完成。下一业务阶段为 Phase 2：控制平面身份与租户上下文，先实现用户注册/登录、租户成员关系、RequestContext、用户级 API Key 哈希存储/创建/撤销，再以跨租户负向测试作为阶段门槛。
