# Phase 6：完整知识库与 RAG 检索实施规格

- 开发分支：`feat/phase-6-knowledge-base-rag`
- 基线：Phase 5C（`feat/phase-5c-idempotent-metering-settlement`）
- 状态：规划与架构基线已建立，功能开发按本文件分批验收
- 商业化边界：不接支付、不扣余额、不产生真实 Embedding 费用

## 1. 产品目标

将现有仅有“知识库名称/描述/Persona 关联”的空壳，升级为可投入 AI 客服和多租户 Agent 使用的完整知识库系统：

1. 用户可以创建知识库，配置 Embedding 模型、切片和检索策略。
2. 用户可以上传文件或导入公开网页 URL。
3. 文件原件安全存入 S3 兼容对象存储，后台 Worker 异步解析、切片、向量化。
4. 用户可查看文档状态、处理日志摘要、重试、重新索引和删除。
5. 用户可直接检索并查看命中片段及其来源。
6. Persona 关联知识库后，OpenAI 兼容 Chat Completions 自动执行 RAG。
7. 回答可返回真实来源引用，非流式与 SSE 语义一致。
8. 严格执行 tenant / knowledge_base / document 级隔离。

## 2. 现有基础与缺口

已存在：
- `knowledge_bases`：租户级知识库基本信息。
- `persona_knowledge_bases`：Persona 与知识库的多对多关系，数据库约束已限制同租户引用。
- PostgreSQL 16 + pgvector 扩展已在初始迁移启用。
- S3 兼容对象存储服务（开发 Compose 中为 Silo）。
- 已有用户级 ModelProvider（加密 API Key）和平台模型凭据托管机制。
- Persona 已可绑定多个知识库。

尚缺：
- 文档、文档版本、导入任务、切片和 Embedding 数据表。
- S3 存储封装、上传与 URL 导入。
- 可靠的异步 Worker 与任务重试/租约。
- 文件解析器、chunker、embedding provider。
- 向量/词法混合检索、RAG 网关注入和引用输出。
- 知识库管理、文档状态、检索调试控制台。

## 3. 目标架构

```text
Console / Client
  ├─ Upload file / Import URL
  ├─ Knowledge base settings
  └─ Search playground
       │
       ▼
FastAPI Control Plane
  ├─ Tenant-scoped Knowledge API
  ├─ Document API + signed upload/download policy
  ├─ Search API
  └─ OpenAI-compatible Gateway
       │
       ├──────── PostgreSQL + pgvector
       │           ├─ knowledge_bases
       │           ├─ knowledge_documents
       │           ├─ knowledge_ingestion_jobs
       │           ├─ knowledge_chunks
       │           └─ knowledge_usage_records
       │
       ├──────── S3-compatible object storage
       │           └─ original files / extracted artifacts
       │
       └──────── Worker service
                   ├─ safe URL fetch
                   ├─ parsers
                   ├─ chunking
                   ├─ embedding batches
                   └─ retry / dead-letter handling

Chat Completions
  ├─ Resolve Persona and its authorized KBs
  ├─ Retrieve (vector + lexical)
  ├─ Reciprocal Rank Fusion
  ├─ Build untrusted reference context
  ├─ Call chat model
  └─ Return citations in SuperNova response extension
```

## 4. Embedding 模型与凭据

- Embedding 与 Chat 是不同模型能力，不能把 Chat 模型目录误当成 Embedding 模型目录。
- 支持平台托管 Embedding 模型（credential_ref 由服务端解析）和 BYOK Embedding（复用当前租户加密保存的 ModelProvider）。
- 知识库保存 embedding_source、模型引用、上游模型名、维度和模型配置版本。
- 单个知识库的所有 chunks 必须使用同一模型配置和维度；切换模型时建立新索引版本，完成后原子切换，禁止混合不同向量空间。
- Embedding 请求使用 OpenAI-compatible `POST /embeddings`；必须校验响应条数、向量长度、有限数值和 usage。
- 不将密钥返回控制台、写入日志或写入文档元数据。
- Embedding 用量先写独立的知识库用量记录，仅作运营与 shadow 统计。

## 5. 文档生命周期与异步任务

状态机：
- document：queued → processing → ready / failed；删除时进入 deleting → deleted。
- job：queued → running → succeeded / retry_wait / dead_letter / cancelled。
- Worker 使用 PostgreSQL `FOR UPDATE SKIP LOCKED` 领取任务，写入 worker_id、lease_until、attempts 和 heartbeat。
- 任务过期可重新领取；达到最大重试次数进入 dead_letter。
- API 不依赖进程内 BackgroundTasks 保证任务持久性。
- 每个文件以 SHA-256 幂等；重复文件不重复切片/向量化。
- 重新索引采用 index_version；新版本成功后切换 active_index_version，失败不破坏当前可用索引。

## 6. 支持的内容类型

第一批：
- PDF（保留页码）
- DOCX（标题/段落/表格）
- PPTX（幻灯片编号）
- XLSX（工作表/单元格范围）
- TXT、Markdown、CSV
- HTML 和公开网页 URL

明确限制：
- 不执行宏、脚本或嵌入式可执行内容。
- 不在 API 进程中同步解析大文件。
- 文件扩展名、MIME 和 magic bytes 三重校验。
- 对上传字节、网页下载字节、页面数量、解析时间和 chunk 数设置上限。
- 旧版二进制 DOC、加密 PDF、扫描件 OCR 作为后续可插拔能力；当前版本明确报不支持或需要 OCR，不静默生成空文档。

## 7. 切片与元数据

- 按结构优先切分：标题、段落、表格、页、幻灯片、工作表。
- 可配置 chunk_size、chunk_overlap、separator 策略。
- chunk metadata 至少包含 document_id、document_version、index_version、chunk_index、page、sheet、slide、heading_path、source_url。
- 不同文档类型的来源字段可为空，但引用必须能定位原始文件和对应位置。
- 生成稳定 chunk hash，重试与重复导入不产生重复活动索引。

## 8. 检索与 RAG

- 语义检索：pgvector cosine distance。
- 词法检索：PostgreSQL trigram / 关键词检索，兼顾中文和英文。
- 混合排序：Reciprocal Rank Fusion；保留 vector_rank、lexical_rank、final_score。
- 参数：top_k、score_threshold、candidate_k、document_ids、metadata filters。
- Reranker 为可插拔接口；初版可以关闭，不强制额外模型调用。
- 检索只访问 Persona 显式关联且当前租户拥有的知识库。
- RAG 上下文标注为不可信引用材料，并明确禁止遵从文档内的指令或提示词注入。
- 返回 citations：document_id、document_name、page/sheet/slide、chunk_id、score、snippet、source_url。
- 非流式响应返回 `supernova.citations`；SSE 在最终完成 chunk 返回相同扩展信息。
- 无命中时 citations 为空；不得虚构来源。
- 检索失败策略可配置，默认在 KB 未就绪时返回明确可观测的状态，不跨租户、不静默使用其他资料。

## 9. 安全要求

- 所有 document/chunk/job/search 查询都同时带 tenant_id 与 knowledge_base_id 过滤。
- 复合外键确保 document、chunk、job 与 KB 属于同一租户。
- URL 导入只允许 HTTP(S) 公网地址；阻止 loopback、私网、链路本地、保留地址和云元数据地址；禁止自动跟随重定向或逐跳重新校验。
- 使用 `trust_env=False`，限制响应大小和超时。
- S3 object key 使用服务端生成的随机标识，不直接使用用户文件名作为路径。
- 删除文档时先使其不可检索，再异步删除对象与索引；保留审计事件，不记录正文。
- 不把原始文档、完整 chunk、API Key、签名 URL写入普通日志。
- 文档内容视为不可信输入，RAG prompt 明确隔离指令注入。

## 10. 资源计量边界

Phase 6 仅记录：
- 原始文件 bytes、对象存储 bytes。
- 成功/失败导入次数、解析耗时。
- Embedding tokens、Embedding 请求次数。
- 活动 chunks、检索次数、命中数。
- RAG chat 请求数与引用数。

这些数据不自动转化为应收款。支付、充值、余额扣减、超额结算留到 Phase 5D 最后实施。

## 11. 分批交付与验收

### Phase 6A — 数据层与可靠文档摄取
- Alembic 新增文档、任务、chunk、usage、索引版本表。
- S3 storage adapter 与配置。
- 上传/URL 导入、文档列表/详情/删除/重试。
- Worker 租约、重试、死信、健康检查。
- 解析器、结构化 chunking、SHA-256 幂等。
- 验收：文件上传/URL 导入可完整走到 ready；API/Worker 重启不丢任务；跨租户访问返回 404。

### Phase 6B — Embedding 与索引
- 平台/BYOK Embedding Provider resolver。
- 批量 Embedding、维度/响应校验、模型版本快照。
- pgvector 持久化、索引版本切换、删除与重建。
- 验收：Embedding 失败可重试；不同模型维度不会混用；旧版本索引在新版本成功前持续可用。

### Phase 6C — 混合检索与 RAG 网关
- Search API、向量/词法检索、RRF、来源定位。
- Chat Completions 自动检索并注入；非流式/SSE 引用一致。
- 检索调试接口和 prompt-injection 防护。
- 验收：中文/英文检索集 Recall@K、MRR、引用准确率和租户隔离测试通过。

### Phase 6D — 控制台与生产验收
- 知识库配置页、文件管理、任务状态、失败重试、检索调试。
- 管理员用量、队列与失败任务监控。
- 端到端测试、Docker Compose Worker 冒烟、迁移升级/回滚说明。
- 验收：完整用户流程可在控制台完成；CI 测试、构建和迁移通过。

## 12. 不在 Phase 6 的事项

- 支付网关、充值、钱包、真实扣费、退款和发票。
- AstrBot Worker 运行时及微信渠道自动化。
- OCR、音视频转录、复杂表格 Agent、图像多模态索引（先留扩展点）。
