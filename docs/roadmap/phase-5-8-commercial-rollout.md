# SuperNova 后续开发路线（知识库优先，支付最后）

## 产品决策

- 默认使用平台模型，同时允许用户 BYOK。
- SaaS 订阅与资源收费方向保持不变，但**不优先开发支付**。
- 当前首要产品能力是完整知识库和 RAG，让人格/客服/机器人可以基于租户私有资料准确回答。
- Phase 5D 商业化支付、真实扣费和资金操作统一延后至最后阶段。
- 在 Phase 5D 完成并通过财务验收前，所有计价仅为 shadow / dry-run，不对用户真实扣款。

## 当前基线

- Phase 0–4：工程基础、控制台、租户身份、人格/供应商、OpenAI 兼容网关及 Token 用量记录已开发。
- Phase 5A–5C：混合模型、平台模型目录、BYOK、订阅权益、额度预留、幂等计量、影子计价和对账已开发。
- Phase 5B / 5C 仍按 PR 依赖顺序合并；Phase 6 开发分支从 Phase 5C 分支衍生，不跳过依赖。
- Phase 6 继续使用 PostgreSQL + pgvector、S3 兼容对象存储（开发环境 Silo）、现有租户/人格/供应商模型。
- 现有 knowledge_bases 与 persona_knowledge_bases 仅提供知识库元数据和人格关联；文档、解析、切片、Embedding、检索和 RAG 注入尚未实现。

## 新的开发顺序

1. **Phase 6：完整知识库与 RAG 检索（当前优先）**
2. **Phase 7：AstrBot 多租户运行环境**
3. **Phase 8：渠道接入、运营与规模化**
4. **Phase 5D：商业化支付与真实收费（最后实施）**

Phase 5D 编号保留，以便保持既有架构文档和计费设计的可追溯性；编号不代表开发优先级。

## Phase 6 — 完整知识库与 RAG 检索

### 6A：知识库与文档生命周期
- 知识库支持 Embedding 配置、切片参数、检索参数和启停状态。
- 支持本地文件上传及公开 URL 导入；先支持 PDF、DOCX、PPTX、XLSX、TXT、Markdown、CSV、HTML。
- 文件原件进入 S3 兼容对象存储，数据库保存元数据、校验和、版本和处理状态。
- 文档状态：queued / processing / ready / failed / deleting；支持重试、重新索引和删除。
- PostgreSQL 持久化任务队列，Worker 使用行锁与租约领取任务；不能依赖 API 进程内 BackgroundTasks 保证任务可靠性。
- 文件类型通过扩展名、MIME 和文件签名共同校验；限制文件大小、下载大小、解析耗时和 URL 出站范围。

### 6B：解析、切片与 Embedding
- 解析器统一输出带页码、工作表、幻灯片、标题路径等来源元数据的文本块。
- 可配置 chunk_size / overlap；保留页码、段落、表格和原始文件位置。
- 支持平台托管 Embedding 模型和租户 BYOK Embedding 模型；Embedding 模型与 Chat 模型目录分离或通过明确的 model_kind 区分。
- 首个 pgvector 索引固定为 1536 维；每个知识库固定 Embedding 模型和索引版本，禁止混用不同向量空间。
- 批量 Embedding、失败重试、模型变更后的全量重建索引。
- 记录 embedding tokens、文件字节数、chunk 数和处理耗时，先用于运营与 shadow 计量，不执行真实收费。

### 6C：检索与 RAG
- pgvector 语义检索 + PostgreSQL 词法检索，使用 Reciprocal Rank Fusion 合并排序。
- 可配置 top_k、相似度阈值、知识库范围、文档过滤；预留可插拔 reranker。
- 返回可追溯引用：文档 ID、文件名、页码/工作表/幻灯片、chunk、分数和片段。
- OpenAI 兼容 Chat Completions 自动读取 Persona 关联的知识库，将检索结果作为不可信参考上下文注入；模型不得执行文档中的指令。
- 响应提供 SuperNova 扩展 citations；流式和非流式响应都要有一致的引用语义。
- 没有相关资料时不伪造引用；检索服务不可用时按明确配置 fail-open / fail-closed，默认不让无关租户资料泄漏或静默跨租户回退。

### 6D：控制台、运维与质量验收
- 知识库详情页：上传、URL 导入、文档列表、处理状态、重试、删除、重建索引。
- 提供检索调试：输入问题、查看命中片段、来源、分数及最终 RAG 上下文。
- Persona 关联知识库后，网关自动启用 RAG；支持启用/关闭及 top_k 配置。
- 管理员查看任务队列、失败原因、Embedding 用量和存储用量。
- 建立中英文/中文检索评测集，评估 Recall@K、MRR、引用正确率、无答案拒答率和租户隔离。
- 自动化测试覆盖解析器、切片、Embedding provider、混合检索、RAG 注入、SSE、删除、重试、SSRF 和跨租户隔离。

### Phase 6 验收标准
- 文档上传或 URL 导入后，由独立 Worker 异步完成解析、切片和向量化；API 重启不丢任务。
- 同一文件重复上传可按 tenant + knowledge_base + SHA-256 做幂等处理。
- 文档更新和删除后，旧 chunk 不得继续被检索。
- 搜索结果始终限定当前租户及授权知识库；所有检索 SQL 必须包含 tenant_id 和 knowledge_base_id 过滤。
- 检索结果可回溯到原文档位置；模型回答引用必须对应实际检索片段。
- Persona 的 Chat Completions 在关联知识库后能自动 RAG；不关联知识库时保持原有响应行为。
- 文件、URL、解析器、Embedding API 都有超时、大小限制、重试和错误隔离。
- 不把原始文档、API Key 或完整私有内容写入普通日志。
- Phase 6 不接支付，不扣钱包余额，不自动收取 Embedding 费用。

## Phase 7 — AstrBot 多租户运行环境

- Worker/机器人实例生命周期、独立 Persona/知识库/插件/渠道配置。
- 任务队列、并发控制、心跳、重启、日志和资源隔离。
- 实例小时数、并发数和任务量只做可观测计量；实际收费仍留在 Phase 5D。

## Phase 8 — 渠道接入、运营与规模化

- 微信已验证接入链路产品化，并逐步扩展企业微信及其他渠道。
- 企业工作空间、成员权限、协作、渠道状态、运营报表、风控、告警和商用部署。
- 形成渠道、知识库、人格和 Worker 的统一运营视图。

## Phase 5D — 商业化支付与真实收费（最后实施）

只有 Phase 6–8 的核心产品能力和资源计量稳定后，才启动：
- 支付渠道适配、签名校验、Webhook inbox 与幂等。
- 套餐购买、升级、续费、取消、充值和退款。
- 预付余额、超额消费授权、消费上限、账单与发票。
- 财务对账、收入报表、退款和异常资金操作审计。
- 支付沙盒成功/失败/超时/重复/乱序回调全量验收。
- 生产真实收费需单独审批；默认关闭超额自动扣费。

## 价格与计量原则

- 套餐、模型价格、Embedding 价格、资源 SKU 均采用版本化配置，不写死在代码中。
- Phase 6–8 可以采集文档字节、Embedding Token、chunk、检索次数、Worker 时间等用量，但只做运营统计或 shadow。
- 不对同一份资源重复收费；文档处理、Embedding、Chat Token 和 Worker 资源需定义清楚的计量边界。
- 在 Phase 5D 上线前，不把 shadow 账本当作真实应收款或钱包余额。
