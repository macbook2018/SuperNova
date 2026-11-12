# SuperNova Phase 5C 实施报告

- 开发分支：`feat/phase-5c-idempotent-metering-settlement`
- 基线分支：`feat/phase-5b-platform-model-routing`
- 数据库迁移：`0007_phase_5c_metering`
- 计费模式：仅影子计价（shadow），不产生真实扣款

## 1. 本阶段交付

### 幂等请求
- OpenAI 兼容 `POST /v1/chat/completions` 支持 `Idempotency-Key`，长度 1–128 个可见 ASCII 字符。
- 未提供该 Header 时使用本次请求的 Request ID；需要客户端跨网络重试去重时，必须自行复用 Idempotency-Key。
- 相同租户、相同 Key、相同请求指纹：非流式成功响应重放已保存的 JSON，不再次调用上游。
- 相同 Key 对应不同请求指纹：返回 409 `idempotency_key_conflict`。
- 请求仍处理中、结果未知或已处理但没有可重放响应（包括 SSE）：返回明确 409，不重复调用供应商。
- 事件、评级、账本分别通过数据库唯一键防止重复创建。

### 并发资源预留
- PostgreSQL 按租户行加锁，再锁订阅与预留记录，确保同一租户的额度预留串行化。
- 每次调用预留 `api_requests=1`；平台模型额外预留 `platform_model_requests=1`、输入 Token 上限和输出 Token 上限。
- 输入 Token 预留采用序列化上游消息的 UTF-8 字节数加消息开销的保守估算；输出 Token 预留采用实际发送的 max_tokens。
- BYOK 只预留 API 请求额度，不预留平台模型 Token 额度。
- 已预留和未知状态的额度都计入剩余额度，避免并发透支。
- 未使用的预留在成功结算或明确失败后释放。

### 可信用量与影子计价
- 只有 prompt_tokens、completion_tokens 都是非负整数，且 total_tokens（如存在）与二者之和一致，才视为可信 usage。
- 平台模型输入和输出分别按请求时快照的价格版本计价。
- BYOK Token 记录为 unmetered，平台模型 Token 金额恒为 0。
- 评级金额使用 Decimal，存储为货币最小单位的十进制金额；账本分录按币种最小单位使用 HALF_UP 舍入。
- JPY/KRW 等零小数币种、BHD/KWD 等三小数币种及 CLF/UYW 等四小数币种使用独立精度配置。
- 影子账本每笔交易生成成对正负分录，并在同一数据库事务内校验借贷平衡。
- 不创建钱包扣款、不变更真实余额、不产生实际应收款。

### 未知 usage 与人工核对
- 上游 5xx、连接中断、超时、流式中断或成功响应缺少可信 usage 时，不猜测 Token 数量、不生成 Token 费用。
- API 请求和平台模型请求计量可以确认时照常记录；未知 Token 预留保持 unknown 并继续占用额度。
- 管理员可查看 unknown 预留；从供应商账单核实后可人工补录输入/输出 Token 完成影子结算，或填写原因释放预留。
- 超时且没有关联 UsageRecord 的预留不能补录 Token，只能经人工核对后释放。

### 对账和控制台
- 租户控制台增加影子估算、平台/BYOK Token 数、未知预留、待评级事件和最近影子账本。
- 管理员可以将超时 held 预留转为 unknown、查看未知预留、人工 settle/release。
- 对账报告核查：借贷是否平衡、评级金额与账本正向分录是否一致、缺失/未评级事件、未知预留、超时预留及预留超额。
- 日报通过 `POST /admin/billing/reconciliation/run` 执行，可由外部 Cron/调度器每日调用。

## 2. 数据库迁移 0007

基于 Phase 5B 的 `0006_phase_5b_routing`：

- 扩展 `billing_reservations`：请求指纹、来源、订阅/套餐/价格版本快照、UsageRecord 关联和非流式响应缓存。
- 新增 `billing_reservation_items`：按资源记录 reserved / consumed / held / unknown / released。
- `billing_usage_events` 增加 reservation 关联和索引。
- `billing_rated_items` 增加每单位价格快照字段和状态约束。
- `billing_ledger_transactions` 增加 shadow/live 模式字段；Phase 5C 只写 shadow。
- 新增 `billing_audit_events` 和 `billing_reconciliation_runs`。
- 为预留、事件、评级和账本查询增加必要索引及约束。

部署：

```bash
alembic -c apps/api/alembic.ini upgrade head
```

## 3. 新增/扩展 API

### 租户接口
- `GET /billing/usage`：当前订阅周期的影子估算和平台/BYOK Token 汇总。
- `GET /billing/ledger`：当前租户自己的影子账本分录。
- `GET /billing/entitlements`：增加 reserved_units，remaining_units 扣除 held/unknown 预留。

### 管理员接口
- `POST /admin/billing/reconciliation/run`
- `GET /admin/billing/reconciliation/runs`
- `POST /admin/billing/reservations/mark-expired-unknown`
- `GET /admin/billing/reservations/unknown`
- `POST /admin/billing/reservations/{reservation_id}/settle`
- `POST /admin/billing/reservations/{reservation_id}/release`

管理员接口继续使用 `SUPERNOVA_PLATFORM_ADMIN_EMAILS` 白名单保护。人工 settle/release 必须提供原因并写入审计事件。

## 4. 首次启用与运维顺序

1. 先在测试环境执行 Alembic 0007 迁移。
2. 配置平台模型、有效价格版本、套餐和订阅；不要在 Phase 5C 打开真实扣费。
3. 客户端对可能重试的聊天请求生成唯一 Idempotency-Key，并在同一业务请求的重试中复用。
4. 先观察控制台的 reserved_units、shadow usage 和账本。
5. 每日调用对账接口，查看 status 与 report_json。
6. 对 unknown 预留先查供应商账单；能够核实 usage 时人工 settle，无法核实时记录原因后 release。
7. 不要直接删除 unknown 预留、评级记录或账本分录。

## 5. 明确不在 Phase 5C 的能力

- 不执行钱包余额扣减或真实超额收费。
- 不接支付、订阅自动续费、充值、退款或发票。
- 超额请求继续 block；不会因 `overage_enabled` 配置而自动扣款。
- 日报接口尚未内置定时 Worker，需要由部署环境中的 Cron/调度器调用。
- 流式请求不保存完整 SSE 响应；重复提交相同 Key 会收到 409，避免二次调用。
- 未知 usage 的自动供应商账单拉取/自动匹配留待后续运营集成。

## 6. 安全边界

- 平台 Key 不返回租户 API/控制台，不写入 UsageRecord 或影子账本。
- BYOK 与平台模型路由继续隔离。
- 幂等 Key 按 tenant 作用域隔离；请求指纹包含人格、模型来源、价格版本及实际转发 Payload。
- 未知 Token usage 不做推测性计费。
- 所有 Phase 5C 评级与账本都标记为 shadow，不可作为真实应收款或钱包余额依据。


## 7. CI 验收记录

最终开发分支提交 `ec5f51a28e86535b518b4dd0a30655ef64fb449d` 的 GitHub Actions 全部通过：

| 检查项 | 结果 |
|---|---|
| Ruff API lint | 通过 |
| API 自动化测试 | **68 passed** |
| PostgreSQL 并发额度预留集成测试 | 通过（包含在 API 测试中） |
| Alembic upgrade head | 通过 |
| Docker Compose build / smoke test | 通过 |
| Console TypeScript / production build | 通过 |

GitHub Actions：[43229056315](https://github.com/macbook2018/SuperNova/actions/runs/43229056315)

测试包含相同幂等 Key 重放、不同 Payload/不同 API Key 冲突、预留额度阻止透支、平台模型 shadow rating、平衡账本、未知 usage 人工补录、租户账单 API、对账和币种精度。
