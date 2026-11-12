# SuperNova Phase 5B 实施报告

- 完成日期：2026-11-12
- 开发分支：`feat/phase-5b-platform-model-routing`
- 基线分支：`feat/phase-5-hybrid-billing-foundation`
- 验收提交：`71ae7dbfaa0433e741833db1bc8f3ad21f113b4c`
- GitHub Actions：`43216473504`

## 1. 交付范围

### 平台模型池
- 新增全局平台模型管理接口，支持模型编码、展示名称、供应商类型、上游模型名称、Base URL、credential_ref 和启停状态。
- 平台模型价格按 revision 追加，不允许通过 API 覆盖价格值。
- 同一模型、同一币种的新价格版本生效时，自动关闭上一条开放价格区间。
- 租户只看到已启用、具有当前币种有效价格且服务端凭据可解析的平台模型。
- 平台模型上游地址复用网关已有的目标地址安全校验。

### 人格模型来源
- Persona 支持 `platform` 和 `byok`。
- 新版控制台新建人格默认选择平台模型。
- 旧 API 请求不传 model_source 时仍按 BYOK 处理；历史人格不自动迁移。
- platform 模式由服务端从 PlatformModel 读取上游模型名称、地址和凭据引用，客户端不能覆盖上游模型名称。
- 两种来源切换时清理另一来源的关联字段，并由数据库约束保证引用一致。
- 不允许跨租户引用 BYOK 供应商。

### 统一网关
- `/v1/chat/completions` 和 `/v1/models` 支持双来源路由。
- 非流式和 SSE 流式共用解析路径。
- 不自动从平台模型回退到 BYOK，也不从 BYOK 回退到平台模型。
- UsageRecord 增加 model_source 和 platform_model_id，保留实际供应商类型、上游模型名及 Token 用量。

### 套餐和额度
- 新增套餐创建、套餐查询、订阅查询、管理员手动分配订阅和权益查询。
- 新注册租户自动创建 TenantBillingPolicy；历史租户不做批量回填。
- 有 BillingPolicy 的租户必须有当前有效订阅才能调用模型。
- 没有 BillingPolicy 的历史租户继续 BYOK 兼容；平台模型始终要求有效订阅。
- 额度资源：api_requests、platform_model_requests、platform_input_tokens、platform_output_tokens。
- BYOK 不消耗平台模型 Token 权益，但仍消耗套餐 API 请求额度。
- Phase 5B 超额一律拒绝，不进行真实扣费。

## 2. 新增 API

### 租户 API
- `GET /platform/models`
- `GET /billing/plans`
- `GET /billing/subscription`
- `GET /billing/entitlements`

### 平台管理员 API
- `GET /admin/platform/models`
- `POST /admin/platform/models`
- `PATCH /admin/platform/models/{model_id}`
- `GET /admin/platform/models/{model_id}/prices`
- `POST /admin/platform/models/{model_id}/prices`
- `POST /admin/billing/plans`
- `POST /admin/billing/tenants/{tenant_id}/subscription`

### 现有 API 扩展
- `POST /personas`
- `PATCH /personas/{persona_id}`
- `GET /v1/models`
- `POST /v1/chat/completions`

## 3. 数据库迁移

新增 `0006_phase_5b_routing`，基于 `0005_hybrid_billing`。

- `platform_models` 增加 `base_url` 和查询索引。
- `personas` 增加 model_source、platform_model_id 的一致性约束及索引。
- `usage_records` 增加 model_source、platform_model_id、来源检查约束及索引。
- 历史 UsageRecord 通过迁移默认归类为 BYOK。
- 不自动创建历史租户订阅、不回填套餐、不迁移旧人格。

部署时执行：

```bash
alembic -c apps/api/alembic.ini upgrade head
```

## 4. 环境配置

新增：

```dotenv
SUPERNOVA_PLATFORM_ADMIN_EMAILS=admin@example.com
SUPERNOVA_PLATFORM_CREDENTIALS_JSON={"deepseek-primary":"REPLACE_WITH_REAL_SECRET"}
```

- 管理员邮箱为逗号分隔白名单；必须使用该邮箱登录后才能调用平台管理 API。
- `SUPERNOVA_PLATFORM_CREDENTIALS_JSON` 是服务端秘密配置，键为数据库中的 credential_ref，值为真实供应商 API Key。
- 不要把真实 Key 写入 Git、数据库、前端或日志。
- `PlatformModelPrice` 的价格字段单位是最小货币单位/百万 Token，例如 CNY 的 fen；控制台显示时转换为元。

## 5. 首次启用顺序

1. 配置平台管理员邮箱和服务端模型凭据，并重启 API。
2. 管理员创建 PlatformModel，设置 credential_ref 和 Base URL。
3. 管理员创建当前币种的模型价格版本。
4. 确认凭据可解析后启用模型。
5. 管理员创建 active BillingPlan，配置四类资源权益。
6. 管理员为目标 tenant 分配有效订阅。
7. 用户在控制台创建人格并选择平台模型，或选择 BYOK。
8. 使用 OpenAI 兼容 API 分别验证两种来源，并检查 UsageRecord。

本阶段不包含套餐购买、支付回调、充值、退款或自动续费；订阅由管理员手动分配。

## 6. 验收结果

GitHub Actions 工作流 `43215697087`：

| 检查项 | 结果 |
|---|---|
| Ruff API lint | 通过 |
| API 自动化测试 | 60 passed |
| Alembic upgrade head | 通过 |
| Docker Compose build / smoke test | 通过 |
| Console TypeScript / production build | 通过 |

测试覆盖平台模型管理、价格版本滚动、平台与 BYOK 调用、人格双向切换、管理员权限、订阅分配、额度超限、历史租户兼容和凭据不泄露。

## 7. 已知边界与 Phase 5C

额度检查当前是调用前读取订阅周期用量并拒绝超额，不包含并发安全的预留。并发请求可能同时通过预检查，因此不得把 Phase 5B 当作最终防透支结算系统。

以下能力留给 Phase 5C / 5D：
- 幂等计费事件；
- 额度预留、释放和并发防透支；
- 账本、消费明细和影子计价；
- 真实超额结算；
- 订阅支付、充值、退款和正式结算。
