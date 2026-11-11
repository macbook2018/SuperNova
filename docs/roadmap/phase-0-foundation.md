# Phase 0：工程基础与可运行骨架

- 状态：In Progress
- 目标：建立可重复启动、可测试、可扩展的仓库基础，不提前堆叠未验证的业务模块。

## 已提交内容

- 项目 README、产品原则、技术基线和交付顺序。
- 控制平面 / 运行平面架构说明。
- 多租户隔离、安全边界和两项架构决策。
- FastAPI 最小服务、Request ID 中间件和健康检查端点。
- PostgreSQL + pgvector、Redis、MinIO 的 Docker Compose 开发环境。
- Python lint / test 的 GitHub Actions 工作流。

## 验收标准

### 工程与运行

- [ ] docker compose -f deploy/docker/compose.yaml config 配置校验通过。
- [ ] docker compose -f deploy/docker/compose.yaml up --build -d 可启动基础服务。
- [ ] API /health/live 返回 200。
- [ ] API /health/ready 在 PostgreSQL、Redis 正常时返回 200；依赖不可用时返回 503。
- [ ] 每个 API 响应包含合法的 X-Request-ID。
- [ ] 容器以非 root 用户运行。

### 质量

- [ ] Ruff 检查通过。
- [ ] pytest 全部通过。
- [ ] GitHub Actions CI 通过。
- [ ] 不存在提交到仓库的真实密钥或本地数据卷。
- [ ] 提供启动、停止、日志和测试命令。

## 本阶段明确不做

- 不重复验证已完成的微信收发 PoC。
- 不修改 AstrBot 上游核心。
- 不在缺少租户身份与权限边界的情况下先写人格、知识库或会话业务 API。
- 不将本地 Compose 通过等同于生产环境就绪。

## 下一阶段入口

Phase 0 验收后进入控制平面：用户登录、租户上下文、用户级 API Key 的安全存储、轮换和撤销，以及跨租户负向测试。
