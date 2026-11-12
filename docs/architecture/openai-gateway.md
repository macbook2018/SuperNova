# OpenAI 兼容网关架构

## 请求链路

~~~mermaid
flowchart TD
  A[第三方 OpenAI SDK] --> B[POST /v1/chat/completions]
  B --> C[校验用户级 API Key]
  C --> D[解析 user_id / tenant_id]
  D --> E[按 tenant + model slug 查询人格]
  E --> F[校验人格与供应商启用状态]
  F --> G[解密供应商凭据]
  G --> H[注入人格提示词并限制生成参数]
  H --> I[调用 OpenAI 兼容供应商]
  I --> J[返回 JSON 或 SSE]
  J --> K[写入 usage_records]
~~~

## 认证模型

API Key 是用户级凭据，不绑定人格。Key 的 SHA-256 摘要用于数据库查找，认证成功后检查：

- Key status 与 expires_at
- User.status
- Tenant.status
- TenantMembership.status

租户上下文完全来自 Key 对应的数据库记录。model slug 查询必须附带 tenant_id 条件。

## 人格路由

请求中的 model 对应 Persona.slug。网关加载 Persona 与同 tenant 的 ModelProvider，并校验：

- Persona.enabled
- Provider.enabled
- Provider.api_key_ciphertext 存在
- Provider 类型可由当前 OpenAI Chat Completions adapter 处理

实际发送给供应商的 model 使用 Persona.model_name；响应返回给客户端的 model 统一替换为 Persona.slug。

服务端始终将 Persona.system_prompt 放在 messages 最前面。调用方可追加 system/developer 消息；调用方不能修改数据库中保存的人格配置。

## 参数限制

- 请求 temperature/top_p 可覆盖人格默认值。
- 请求 max_tokens 可降低输出上限，但最终值不超过 Persona.max_tokens。
- 暂不支持的字段通过 Pydantic extra=forbid 返回 422，不静默丢弃。

## 流式响应

网关请求供应商返回 SSE，并请求 stream usage。解析每个 data chunk，将响应 model 改写为人格 slug。收集最终 usage chunk；正常结束时发送 [DONE]。若供应商流中断，发送 SSE error 事件并记录失败状态。若客户端中断，记录 cancelled 状态。

## 用量数据

usage_records 不保存 messages 或 completion 文本。每次完成/失败/取消的上游调用记录一次，Token 数仅使用供应商明确返回的 usage。没有 usage 时 total_tokens 保持 NULL，并增加 unmetered_requests 计数。

## 出站网络安全

- 供应商 Base URL 仅允许 HTTP(S)；生产环境必须 HTTPS。
- 不跟随重定向，不继承 HTTP_PROXY / HTTPS_PROXY 环境代理。
- DNS 解析结果必须全部为公网地址；开发环境仅显式 localhost / loopback 例外。
- 生产环境限制标准 HTTPS 端口。
- 不把供应商 API Key 写入日志或返回前端。
