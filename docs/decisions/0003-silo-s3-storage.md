# ADR 0003：开发环境使用 Silo 提供 MinIO 兼容的 S3 存储

- 状态：Accepted
- 日期：2026-11-11

## 背景

原开发环境使用 `quay.io/minio/minio:latest`。CI 实际拉取时返回 401 Unauthorized，导致整个 Compose 冒烟测试无法启动。MinIO 社区镜像的公开分发渠道在近期发生变化，不能再把匿名拉取旧镜像作为可靠前提。

## 决策

1. 开发环境改用 `docker.io/pgsty/silo:RELEASE.2026-11-09T00-00-00Z`。
2. Silo 是独立社区维护的 MinIO 分支，兼容 MinIO 常用的 S3 API、`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` 配置和服务启动参数。
3. 固定镜像版本，不使用滚动的 `latest` 标签。
4. SuperNova 应用层只依赖标准 S3 API；不依赖 Silo 私有 API 或实现细节。
5. Compose 中暂时保留 `minio` 服务名和 `MINIO_*` 环境变量，降低现有开发配置迁移成本。

## 后果

- 本地开发和 CI 不再依赖当前不可匿名拉取的 MinIO 官方镜像地址。
- Silo 是社区分支，不是 MinIO, Inc. 官方发行版；生产部署前仍需单独评估维护状态、许可证、安全更新和兼容性。
- 后续可在不改变应用层存储接口的前提下替换为其他 S3 兼容服务。
