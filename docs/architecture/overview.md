# 系统架构总览

## 目标

SuperNova 采用控制平面（Control Plane）与运行平面（Runtime Plane）分离的架构。控制平面管理 SaaS 业务资源；AstrBot Worker 承担 Agent 执行。统一模型网关为外部业务提供稳定的 OpenAI 兼容接口。

## 逻辑组件

~~~mermaid
flowchart TB
    Client[业务系统 / OpenAI SDK] --> Gateway[统一 API Gateway]
    Console[SuperNova Console] --> Control[FastAPI Control Plane]
    Gateway --> Auth[API Key 身份识别]
    Auth --> Persona[人格与权限解析]
    Persona --> Router[模型 / Agent 路由]
    Router --> Worker[租户隔离 AstrBot Worker]
    Worker --> Provider[模型供应商]
    Worker --> KB[知识检索服务]
    Control --> DB[(PostgreSQL + pgvector)]
    Gateway --> DB
    Control --> Redis[(Redis)]
    Control --> Storage[(S3-compatible object storage)]
    Control --> Manager[Worker Manager]
    Manager --> Worker
~~~

## 控制平面职责

- 用户、租户、成员与权限管理。
- 用户级 API Key 的创建、轮换、撤销和审计。
- AI 人格配置、模型供应商及模型凭据管理。
- 知识库、文档、索引和检索配置。
- 渠道、会话、用量、套餐与计费记录。
- Worker 创建、配置下发、健康检查、重启和回收。

## 运行平面职责

- 使用 AstrBot 执行 Agent 对话。
- 加载人格、插件、知识库检索适配器和渠道适配器。
- 通过内部协议调用模型网关或受控模型供应商。
- 上报会话事件、Token 用量、运行状态和错误。

## 关键请求链路

1. 客户端向统一 Base URL 发送请求，并携带用户级 API Key。
2. Gateway 校验 Key，解析 user_id、tenant_id、状态和权限。
3. Gateway 根据请求中的 model 找到该用户可用的人格；客户端不能传入任意 worker_id。
4. Gateway 将规范化请求路由至该人格所在的 AstrBot Worker。
5. Worker 执行 Agent、知识检索和模型调用。
6. Gateway 将结果转换为 OpenAI 兼容响应；流式请求以 SSE 返回。
7. 用量、耗时、请求 ID 和错误状态写入审计及计量记录。

## 设计边界

- 控制平面不直接承载长生命周期 Agent 进程。
- Worker 不直接访问其他租户的数据库记录或对象存储前缀。
- 外部 API 不暴露 Worker 内网地址和内部凭据。
- AstrBot 上游升级应尽量通过适配器兼容，不把业务逻辑长期维护在上游 Fork 中。
- 已完成的微信收发 PoC 不重复建设；进入渠道阶段时将其作为回归验收基线。
