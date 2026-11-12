# Phase 4：统一 OpenAI 兼容 API 网关

- 状态：实现中，等待 CI 全量验收与真实供应商联调
- 基础分支：feat/phase-3-persona-provider
- 实现分支：feat/phase-4-openai-gateway

## 目标

让用户创建的人格可以通过统一 OpenAI 兼容 Base URL 被第三方程序调用。每个用户使用一把用户级 API Key；请求中的 model 字段选择人格 slug。网关负责身份认证、租户范围内的人格解析、模型供应商路由、流式输出和 Token 用量记录。

## 调用约定

Base URL：

~~~text
https://<supernova-domain>/v1
~~~

认证：

~~~http
Authorization: Bearer sn_live_<user_api_key>
~~~

### 模型列表

- GET /v1/models
- 只返回当前 API Key 所属工作空间中已启用、供应商已启用且已配置凭据的人格。
- data[].id 为人格 slug，不是供应商模型名称。

### 对话补全

- POST /v1/chat/completions
- model 必须填写当前用户的人格 slug。
- 服务端自动注入人格 system_prompt，并使用人格配置的 provider、model_name、temperature、top_p、max_tokens。
- 请求可覆盖 temperature、top_p 和 max_tokens；max_tokens 永远不会超过人格配置的 max_tokens。
- 支持 stream、stop、presence_penalty、frequency_penalty。
- stream=true 使用 SSE，最后发送 data: [DONE]。

示例：

~~~bash
curl "$SUPERNOVA_BASE_URL/v1/chat/completions" \
  -H "Authorization: Bearer $SUPERNOVA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "customer-support",
    "messages": [{"role": "user", "content": "你好"}]
  }'
~~~

流式示例只需在请求 JSON 中增加 "stream": true。

## 认证与隔离

- API Key 使用现有 sn_live_ 用户级密钥格式。
- 服务端按 SHA-256 摘要查找 Key，并重新验证 Key 状态、有效期、User、Tenant 和 Membership。
- Key 不绑定 persona；所有人格共用用户级 Key。
- 人格 slug 仅在当前 tenant 范围内解析；客户端不能传入 tenant_id、user_id、provider_id 或内部 worker ID。
- 已撤销或过期 Key 返回 OpenAI 风格 401。
- API Key 的 last_used_at 在成功认证后更新。

## 模型供应商与网络安全

- DeepSeek 官方 OpenAI 兼容 Base URL 使用 `https://api.deepseek.com`，网关请求路径为 `/chat/completions`，不自动插入 `/v1`。
- 其他 OpenAI 兼容供应商：Base URL 若已包含 `/v1`，网关追加 `/chat/completions`；若未包含 `/v1`，网关追加 `/v1/chat/completions`。

- 当前对话适配器支持 OpenAI Chat Completions 兼容协议；适用于 OpenAI、DeepSeek、Qwen 和其他兼容供应商。
- 当前阶段不支持 Anthropic 原生 Messages 协议；选择 Anthropic 类型的人格会收到明确的 provider_not_supported 错误，不会伪装为兼容成功。
- 供应商 API Key 在服务端 Fernet 解密，只通过服务端到服务端 Authorization Header 发送。
- 不跟随供应商 HTTP 重定向，不使用环境代理。
- 网关解析供应商 DNS 并阻止非公网地址；开发环境仅允许显式 localhost 回环地址走 HTTP。生产环境要求 HTTPS 与标准端口。
- 请求日志不记录对话正文或供应商密钥。

## Token 用量记录

新增 usage_records 表，记录：

- request_id、tenant_id、user_id、api_key_id
- persona_slug、provider_type、model_name
- prompt_tokens、completion_tokens、total_tokens
- 请求状态、响应耗时、错误代码和创建时间

用量接口（JWT 登录态）：

- GET /usage/summary：返回本月与累计请求数、成功/失败数、输入/输出/总 Token、未返回 usage 的请求数。
- GET /usage/records：返回当前用户最近调用记录，支持 limit / offset。

计量规则：

- 非流式请求读取供应商响应中的 usage。
- 流式请求要求供应商返回最终 usage chunk。
- 供应商没有返回 usage 时，将请求标记为 unmetered，不估算、不伪造 Token。
- 用量记录只保存元数据，不保存消息正文。

## 当前兼容边界

首期支持纯文本 messages 和常见生成参数。暂不支持 tools/function calling、图像/音频输入、JSON Schema response_format、多个候选 n、Anthropic 原生协议。未知参数返回结构化 422，不静默忽略。

## 计费边界

本阶段完成 Token 用量计量，不等于已经完成金额扣费。尚未配置供应商模型单价、平台加价规则、套餐、余额账户、充值、账单和超额限额。不得根据 Token 数量显示虚构金额或余额。后续计费阶段应基于 usage_records 与版本化价格表计算金额，并设计幂等扣费和失败请求结算规则。

当前单次请求的 max_tokens 由人格配置限制；月度 Token 额度和余额扣费尚未实现。

## 自动化验收标准

1. 无效、撤销、过期 API Key 返回 OpenAI 风格 401。
2. /v1/models 只返回当前用户可调用的人格。
3. 一个用户级 Key 能调用该用户多个不同人格。
4. 跨租户人格 slug 不可访问。
5. 服务端正确注入 system_prompt，并将人格 slug 映射为供应商 model_name。
6. 请求 max_tokens 不得突破人格 max_tokens。
7. 非流式和流式请求都能记录供应商真实 usage。
8. 流式响应包含 data: [DONE]；中断或无法计量时产生明确状态。
9. 供应商密钥不出现在响应、日志或前端。
10. SSRF 私网地址防护、数据库迁移、Ruff、pytest、前端构建和 Docker Compose 冒烟检查全部通过。

## 本阶段待完成

- GitHub Actions 全量通过。
- 使用真实 DeepSeek 或其他 OpenAI 兼容供应商进行非流式、流式端到端验收。
- 用真实 API Key 验证第三方 OpenAI SDK 接入。
- 评估供应商对 stream usage 的支持情况，并记录不返回 usage 的兼容行为。
