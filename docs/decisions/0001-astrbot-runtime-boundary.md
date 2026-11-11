# ADR 0001：AstrBot 作为隔离的 Agent 运行时

- 状态：Accepted
- 日期：2026-11-11

## 背景

SuperNova 需要支持多租户、多人格、知识库、插件和微信渠道。自研完整 Agent 与消息运行引擎会扩大开发范围，并增加与上游生态脱节的维护成本。

## 决策

1. 使用 AstrBot 作为 Agent 执行引擎。
2. SaaS 用户、租户、人格、计量、授权和控制台由 SuperNova 控制平面管理。
3. 通过适配器、内部 API 和 Worker Manager 连接 AstrBot，不把 SaaS 业务逻辑直接写入 AstrBot 核心。
4. 首期按租户部署隔离的 AstrBot Worker 容器。
5. Worker 生命周期由 Worker Manager 管理；控制平面不直接启动或持有 Agent 进程。
6. 已验证的微信 PoC 作为渠道集成的既有基线，不重复做同一轮技术验证。

## 后果

- 可以独立升级控制平面和 AstrBot 运行时适配层。
- 需要建设 Worker 配置同步、健康检查、故障恢复和资源限制。
- 每租户 Worker 会增加基础资源成本，后续可在不破坏隔离边界的前提下评估更高密度部署。
