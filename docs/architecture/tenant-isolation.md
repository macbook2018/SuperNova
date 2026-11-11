# 多租户隔离与安全边界

## 身份模型

用户级 API Key 是外部 API 的身份凭据。每个 Key 必须绑定：

- user_id
- tenant_id
- key_prefix（用于控制台展示和检索）
- key_hash（不可逆验证值）
- status、created_at、last_used_at、expires_at、revoked_at

API Key 明文只在创建成功时展示一次。服务端不得记录明文 Key。

## 请求上下文

认证中间件验证 Key 后生成不可由客户端覆盖的 RequestContext：

- request_id
- user_id
- tenant_id
- api_key_id
- scopes

业务服务只从 RequestContext 获取用户和租户身份。不得信任请求体、查询参数或自定义 Header 中的 tenant_id/user_id。

## 数据隔离

所有租户业务表必须包含 tenant_id，用户私有资源还应记录 owner_user_id。每次读取、更新、删除均需同时约束资源 ID 和授权范围，不能仅凭全局 UUID 查询后直接返回。

pgvector 检索必须在向量相似度查询前或同一 SQL 查询中强制加入 tenant_id 过滤。数据库 Row Level Security 可作为纵深防御，但不能替代应用层授权。

## 存储与缓存隔离

- MinIO 对象 Key 使用 tenant/{tenant_id}/... 前缀；下载前重新验证资源归属。
- Redis Key 使用 tenant:{tenant_id}:... 命名空间；不得跨租户复用会话缓存。
- Worker 配置、临时文件和日志必须带租户边界；不同租户 Worker 不共享可写工作目录。
- Worker 的模型凭据和渠道凭据按最小权限注入，不写入镜像或仓库。

## Worker 隔离

首期按租户隔离 AstrBot Worker 容器。Worker Manager 负责创建、启动、停止、健康检查和回收。容器应配置 CPU、内存、网络和文件系统限制。控制平面仅通过内部服务身份访问 Worker。

## 必须覆盖的安全测试

1. 用户 A 使用自己的 Key 请求用户 B 的 model，必须拒绝。
2. 用户 A 猜测用户 B 的知识库、文档、会话或文件 UUID，必须拒绝。
3. 用户 A 不能通过传入其他 tenant_id 改变数据范围。
4. 已撤销或过期 Key 必须立即失效。
5. Worker A 不能读取 Worker B 的文件、凭据或会话。
6. 向量检索、缓存命中和对象下载均验证租户归属。

## 日志与隐私

日志记录 request_id、tenant_id、user_id、api_key_id、路由、耗时、Token 数和错误代码。不得记录 API Key、模型供应商密钥、微信登录凭据或完整敏感对话内容。
