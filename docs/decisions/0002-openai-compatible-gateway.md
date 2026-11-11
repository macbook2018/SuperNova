# ADR 0002：用户级 API Key 与 OpenAI 兼容统一网关

- 状态：Accepted
- 日期：2026-11-11

## 背景

用户需要通过统一 Base URL 和 API Key 将 SuperNova 接入现有业务系统，同时在一个 Key 下调用多个独立人格。

## 决策

- 使用一个用户级 API Key 识别调用者；Key 在数据库中绑定 user_id 和 tenant_id。
- 请求中的 model 字段选择该用户有权限使用的人格，不直接映射为供应商模型名称。
- GET /v1/models 仅返回当前用户可调用的人格。
- POST /v1/chat/completions 使用 OpenAI Chat Completions 请求结构。
- stream=true 使用 Server-Sent Events；正常结束发送 data: [DONE]。
- Gateway 负责认证、人格授权、路由、超时、错误映射、Request ID 和用量计量。
- 不在浏览器或客户端暴露供应商 API Key。

## 兼容边界

首期兼容常用的 messages、model、temperature、top_p、max_tokens、stream 等字段。对于暂不支持的参数，应返回结构化且可诊断的错误，不得静默忽略会改变语义的参数。

## 安全约束

model 必须在当前用户授权范围内解析。不得允许通过修改 model、额外传入 tenant_id 或猜测内部 ID 跨租户访问资源。
