# SuperNova 混合模型来源与计费架构（Phase 5 总设计）

## 1. 总体架构

~~~mermaid
flowchart TD
  C[第三方客户 / 微信 / AstrBot] --> G[SuperNova API Gateway]
  G --> K[用户级 API Key、身份和租户校验]
  K --> P[按 tenant + model slug 查找人格]
  P --> R{人格 model_source}
  R -->|platform| M[平台模型目录 + 内部秘密仓库]
  R -->|byok| B[租户自有 ModelProvider + 加密 Key]
  M --> A[预检查订阅/额度/预算与资源预留]
  B --> A
  A --> U[统一供应商适配器]
  U --> L[供应商真实 usage/失败事件]
  L --> E[不可变使用事件 + 去重]
  E --> Q[套餐版本 / 模型价格版本 / 超额规则]
  Q -->|仅平台模型计收模型费| S[评级/清算]
  Q -->|BYOK 模型费为 0| S
  S --> W[平衡账本/余额及账单]
  S --> D[租户用量报表]
~~~

认证结构不变：**一把用户级 API Key，多个人格，model 参数为 Persona.slug**。平台模型和 BYOK 在 Persona 的配置中明确标记，不由请求方覆盖。

## 2. 领域模块

- identity：User、Tenant、TenantMembership、ApiKey（沿用现有）。
- catalog：Persona、KnowledgeBase、ModelProvider（沿用现有）；Persona 增加 model_source、platform_model_id。
- platform-model-registry：平台模型 catalog、provider_model_name、只读展示名、秘密引用、价格版本；仅系统管理员可修改。
- subscription：Plan 版本、PlanEntitlement、TenantSubscription、TenantBillingPolicy。
- metering：沿用 usage_records 作为运行遥测；新增带幂等键的 billing_usage_events 作为可结算输入事实。
- rating：依据 **事件来源+模型销售价格版本+套餐额度** 对用量进行评级，区分 included/overage/unmetered。
- ledger：不可变账本 Transaction 与 Posting（同一交易借贷总和为零）、余额投影与预留记录。
- payment-adapter：收款/退款适配器、Webhook inbox 与重放保护；支付渠道后选，禁止在浏览器侧标记到账。
- invoice：结算单、账单明细、退款与税费（后续实现）；所有金额保留币种与价格快照。
- admin：模型运营、套餐版本、价格发布、对账和审计，仅管理员可操作。

## 3. 数据库变更：从 0004 到 0005

**新迁移 0005_hybrid_billing_foundation；只增表/增字段，不删除现有用户或业务数据。**

### 3.1 租户与人格兼容字段

personas：
- model_source：VARCHAR(16) NOT NULL，值 platform / byok；**迁移时旧数据默认 byok**，新建默认 platform 由应用层显式赋值。
- platform_model_id：UUID NULL，关联 platform_models；仅 platform 有效。
- 保留原 provider_id、model_name；已有 BYOK 人格路由不变。

tenant_billing_policies：
- tenant_id PK，默认模型来源、currency、overage_enabled、overage_budget_minor、settlement_strategy、updated_at。
- 迁移不为旧租户自动开启收费/自动续费；新租户默认 platform UI，但未有权益不能产生付费调用。

### 3.2 套餐与权益

billing_plans：
- id、code、revision、name、billing_interval(month/year)、currency、subscription_price_minor、status、effective_from/created_at。
- (code, revision) UNIQUE。**发布后不可修改已引用版本**。

billing_plan_entitlements：
- id、plan_id、resource_code、unit、included_units、hard_limit_units、overage_rate_minor_per_unit (NUMERIC)、overage_behavior(block/prepaid/invoice)、UNIQUE(plan_id, resource_code)。
- 不将所有资源硬塞进 Token：例如 platform_model_credit_minor、api_request、kb_storage_gib_hours、document_parse_pages、robot_instance_hours、concurrent_runs。

tenant_subscriptions：
- id、tenant_id、plan_id、status、current_period_start、current_period_end、cancel_at_period_end、created_at。
- 单租户同一时刻只能有一个 active/trialing entitlement 来源；需服务层锁与数据库唯一保护。

### 3.3 平台模型与价格

platform_models：
- id、code、display_name、provider_type、upstream_model_name、credential_ref、enabled、created_at。
- credential_ref 仅服务端引用 Secret Manager；**禁止将 Key 明文、密文或可复原密钥返回控制台用户**。
- code 是平台展示/选择标识，Persona.slug 仍是客户端的 model 参数。

platform_model_prices：
- id、platform_model_id、revision、currency、input_per_million_minor、output_per_million_minor (NUMERIC)、effective_from、effective_until。
- (platform_model_id, revision) UNIQUE；版本时间区间不允许重叠，由发布服务约束并定期核验。
- 将供应商成本单价和平台售出单价分表/分角色隔离，当前表仅记录销售单价。

### 3.4 用量事件、评级与账本

billing_usage_events：
- id、tenant_id、source_usage_record_id、event_key、resource_code、model_source、quantity_units、occurred_at、status、created_at。
- (tenant_id, event_key) UNIQUE；同一用量业务事实重试不可重复创建。
- 平台模型模型费只允许 model_source=platform；BYOK 模型 Token 只显示成本观察值，不生成应收模型费。
- source_usage_record_id 可为空以支持机器人运行时、知识库索引等非模型事件；均需单独的资源事件标识。

billing_rated_items：
- id、usage_event_id UNIQUE、plan_id/price_version_id、included_units、overage_units、amount_minor_decimal、currency、state、rated_at。
- 固化评级当时适用的套餐和单价。若价格缺失、usage 不可得、来源不明，state=unrated 不得扣款。
- 平台模型输入和输出价格不同，应生成不同的 resource_code 事件，如 platform_input_tokens / platform_output_tokens。

billing_ledger_accounts：
- id、account_key UNIQUE、tenant_id nullable、currency、kind、created_at。

billing_ledger_transactions：
- id、idempotency_key UNIQUE、kind、reference_id、currency、created_at、metadata JSON。
- 每个 transaction 至少 2 条 posting 且 **SUM(amount_minor)=0**；在原子事务内校验并落库，不能先记账后调用支付。
  
billing_ledger_postings：
- id、transaction_id、account_id、amount_minor signed BIGINT、created_at。
- 钱包/平台收入/清算科目必须币种一致，禁止写入金额为 0 的 posting。

billing_reservations：
- id、tenant_id、idempotency_key UNIQUE、amount_minor、status、expires_at、settled_at、created_at。
- 充值余额、已预留金额与可用余额必须有并发安全的账本投影；同步预留和钱包余额行锁操作。

### 3.5 后续表（不在当前迁移自动创建）

- invoices / invoice_lines / tax_rules：税费、结算账单、开票。
- payment_orders / payment_webhook_inbox：支付订单、退款回调幂等、支付渠道。
- pricing_change_audits：模型上游成本与客户价格版本变化审计。
- billing_reconciliations：向供应商账单、使用记录、账本定期对账。

## 4. 运行时计费决策

| 场景 | 模型收费 | 平台资源收费 | 行为 |
| --- | --- | --- | --- |
| 平台模型、套餐内 | 套餐权益冲抵 | 按包含额度冲抵 | 允许 |
| 平台模型、超额已授权 | 适用模型价格版本 | 适用资源价格版本 | 先预留额度/余额，再结算 |
| 平台模型、超额未授权 | 不扣款 | 不扣款 | 新请求达到配额后拒绝；不透支 |
| BYOK、套餐内 | 0 | 套餐权益冲抵 | 允许 |
| BYOK、资源超额已授权 | 0 | 仅明确的超额资源项 | 先预留，后结算 |
| BYOK、资源超额未授权 | 0 | 不扣款 | 达到额度后拒绝新请求 |

重要：api_request 和 model_token 是不同资源。若套餐已将 API 请求服务包含在平台模型单价内，不得再针对同一请求重复收取平台 API 请求超额费。需在 SKU 定义中指定互斥/包含关系。

## 5. API 设计（先拟合约，尚未上线）

读接口（需用户 JWT / tenant scope）：
- GET /billing/plans（公众可见价格版本）
- GET /billing/subscription
- GET /billing/entitlements
- GET /billing/usage?period=...
- GET /billing/ledger（用户自己的账户流水）
- GET /billing/quotes（估价仅供展示，非最终账单）
- GET /platform/models（平台可用模型、能力、价格和权益）

写接口（用户 JWT、CSRF/鉴权/幂等/显式授权）：
- PUT /billing/policy（超额开关和预算上限）
- POST /billing/subscriptions（创建/变更套餐，受支付/订阅状态机约束）
- PATCH /personas/{id} 扩展 model_source / platform_model_id / provider_id

管理接口（独立 ADMIN 权限与审计，绝不使用普通 TenantMember 权限）：
- POST /admin/billing/plans
- POST /admin/platform/models
- POST /admin/platform/model-prices
- POST /admin/billing/adjustments（双人审核/审计）
- GET /admin/billing/reconciliation

## 6. 额度和并发的结算状态机

1. Authenticate：验证 Key，确定用户与 tenant。
2. Resolve：加载 Persona.model_source，不允许客户端覆盖；查已发布价格版本。
3. Preflight：核实租户有效订阅与资源使用量、超额开关、预算。
4. Reserve：在数据库事务中锁定/预留足额或保守上限；返回 reservation_id。
5. Execute：调用相应平台供应商或 BYOK 的供应商，向用户返回响应。
6. Meter：得到稳定 usage；流式等待最终 usage chunk / provider usage 报告。
7. Rate：按价格版本与套餐权益计算精确费用；对未知 usage 挂账待对账，不猜测。
8. Settle/Release：事务内做唯一结算、平衡分录、更新聚合；未使用额度释放。
9. Reconcile：异步扫描中断、超时、延迟 usage、支付回调和供应商账单。

所有影响钱的步骤必须幂等，按 (tenant_id, billing_event_key) 去重；重复请求应返回原结果，不会第二次扣款。

## 7. 本地和生产功能开关

- billing_schema_ready：只上线迁移与代码，默认 true。
- billing_shadow_rating_enabled：**默认 false**，启用后只计算/记录试算金额，不更新余额。
- platform_model_calls_enabled：**默认 false**，待平台凭据仓库、预算和预留闭环通过验收再开启。
- real_billing_enabled：**默认 false**，必须经过产品负责人审批、双端对账和支付回调验收才能开启。
- byok_model_surcharge_enabled：**固定 false**，不得通过普通后台绕过业务决策。

## 8. 验收边界

本设计与数据库准备完成，不代表完成了支付、实际扣费、余额保护或平台模型代理。上线前应覆盖：双租户隔离、幂等重放、并发预留、账本平衡、价格版本不可变、换套餐中途处理、流式断连、usage 缺失、不足额、回调重放、账单对账、密钥泄露和数据保留。


## 9. Phase 5B 实际实现边界（2026-11-12）

Phase 5B 在 0006 迁移中为平台模型增加 base_url，为 usage_records 增加 model_source 和 platform_model_id，并为 personas 增加来源一致性约束。旧人格继续 BYOK，不进行自动迁移。

已实现的运行路径：
- platform：Persona.platform_model_id → PlatformModel → 当前租户币种的有效 PlatformModelPrice → credential_ref 对应的服务端凭据 → OpenAI 兼容上游。
- byok：Persona.provider_id（必须属于当前租户）→ ModelProvider → Fernet 解密用户 Key → OpenAI 兼容上游。
- 两条路径都使用相同的 API Key 身份认证、租户隔离和套餐权益预检查；来源由数据库人格配置决定，客户端不能覆盖。
- usage_records 记录来源、平台模型 ID、供应商类型、上游模型名和实际 usage。BYOK 不产生平台模型 Token 费用。
- 新注册租户创建 TenantBillingPolicy。没有 BillingPolicy 的历史租户保留 BYOK 兼容；新租户必须有有效订阅。
- 平台管理员由 SUPERNOVA_PLATFORM_ADMIN_EMAILS 白名单控制；平台凭据由 SUPERNOVA_PLATFORM_CREDENTIALS_JSON 的 credential_ref 映射解析。真实 Key 不写入数据库或返回租户。

Phase 5B 的额度检查是**调用前读取当前订阅周期用量并拒绝超额**，不是并发安全的预留。并发请求可能在同一时刻通过预检查，因此正式商用前必须由 Phase 5C 实现数据库预留、幂等事件和结算；当前不得启用实际超额收费。


## 10. Phase 5C 实际实现边界（2026-11-12）

迁移 `0007_phase_5c_metering` 已将预留、幂等、影子评级与审计落到数据库：

- `billing_reservations` 保存 tenant-scoped idempotency key、请求指纹、订阅/套餐/价格版本快照、UsageRecord 关联和非流式响应缓存。
- `billing_reservation_items` 按 resource_code 记录预留、消耗与 held/unknown/settled/released 状态。
- PostgreSQL 以 tenant 行锁作为同租户预留的串行化点，再锁订阅与 reservation；并以唯一键作为最终幂等保护。
- Chat Completions 接受 `Idempotency-Key`。同 Key 同 Payload 的非流式成功请求重放已缓存 JSON；同 Key 不同 Payload 返回 409；SSE 不缓存完整响应，重复 Key 返回 409，不再次调用上游。
- 平台模型请求预留 API request、平台模型 request、输入 Token 上限和 max_tokens 输出上限；BYOK 仅预留 API request。
- 输入 Token 预留采用序列化上游消息的 UTF-8 字节数加消息开销作为保守估算；最终只按可信供应商 usage 结算。该估算用于防止并发超额，并非最终 Token 计量。
- 可信 usage 要求 prompt_tokens 和 completion_tokens 都是非负整数，total_tokens 如存在必须相符。缺失或不可信时不创建 Token 评级，不推测扣费；平台 Token 预留转 unknown 并继续占用额度。
- 管理员可以基于供应商账单人工补录可信 usage 完成 shadow settle，或提供原因释放 unknown reservation。无 UsageRecord 的过期 held reservation 只能先标 unknown，再人工释放。
- `billing_rated_items` 分别记录输入/输出价格版本、included/overage units、Decimal 金额和币种精度。BYOK Token 为 unmetered。
- Phase 5C 只创建 `mode=shadow` 的双分录账本；每笔正负 posting 原子写入并校验 SUM(amount_minor)=0。不会修改钱包、扣款或产生实际应收。
- 币种最小单位精度集中管理：常见两位小数币种默认2位，JPY/KRW等0位，BHD/KWD等3位，CLF/UYW等4位。
- 对账报告核查不平衡交易、评级/账本金额差异、缺失或 unrated 事件、unknown/stale reservation 和 reservation overrun。日报接口由外部调度器每日调用。

Phase 5C 仍然 block 超额。真实超额收费、余额/钱包、支付回调、退款和自动续费均未启用，属于后续商业化阶段。
