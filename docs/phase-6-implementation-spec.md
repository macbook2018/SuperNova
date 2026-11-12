# Phase 6：完整知识库与 RAG 检索实施规格

## 0. 目标与边界
完成租户可用的知识库闭环：文件/网页导入、解析、标准化、切片、Embedding、索引、混合检索、Persona 授权、上下文注入、引用返回、增量更新和彻底删除。

本阶段不接支付、不启用真实扣费。Phase 5C 的幂等、额度预留和影子计量保持兼容。文档解析、Embedding、存储资源必须独立记录，不得与 Chat Completion Token 重复计费。Phase 5D 商业化支付排在 Phase 6、7、8 全部验收之后。

现有基础：PostgreSQL 已启用 pgvector；已有租户、用户、Persona、Redis、MinIO/S3、FastAPI、SQLAlchemy、Alembic、Docker Compose、结构化日志及 OpenAI 兼容网关。当前代码树未发现完整知识库后端模块，因此必须先审计已有 Persona-KB 前端字段，不能假设 API/ORM 已存在。

## 1. 架构原则
- 所有 KB、文档、版本、Chunk、Embedding、任务、引用记录强制 tenant_id；租户身份从鉴权主体取得，绝不信任请求体 tenant_id。
- Persona 显式关联知识库；默认不向所有 Persona 开放。检索只访问当前 Persona 关联且启用的 KB。
- 上传/URL 接口快速返回，解析、切片、Embedding、索引由独立 Worker 异步执行；任务幂等、可重试、可观测。
- 检索结果必须可追溯到文档版本、页码/标题/URL/段落定位；不允许伪造引用。
- 文档更新采用新版本构建；新版本完全成功后原子切换 active_version，期间旧版本持续可用。
- 对象存储私有；下载必须先做租户归属校验；删除任务可重试并最终清理文件、向量、缓存和索引。

## 2. 技术选型
- API：沿用 FastAPI + SQLAlchemy 2 + Pydantic。
- 向量：PostgreSQL + pgvector。先验证现有 Postgres 镜像和扩展版本；HNSW 支持时启用，否则保留精确检索回退。embedding model/provider/dimension 必须隔离，禁止跨模型向量比较。
- 全文：PostgreSQL tsvector + GIN；默认 Hybrid Retrieval，使用 Reciprocal Rank Fusion 合并向量与关键词排名。Reranker 留适配接口，不设为硬依赖。
- 对象存储：沿用 MinIO/S3 私有 bucket，随机 object key；原始文件和抽取文本均不公开。
- 队列：沿用 Redis；先审计现有 worker/task 基础设施，复用现有机制，避免引入第二套队列。
- Embedding：定义 Provider Adapter，首版至少支持一种 OpenAI-compatible embeddings API；可配置 Base URL、模型、维度、超时、批大小和重试。平台托管与 BYOK 凭据均只在服务端解密使用。
- 解析：首批支持 PDF、DOCX、TXT、Markdown、HTML、CSV；评估 XLSX/PPTX 是否纳入首版。扫描 PDF 若无 OCR，必须明确显示未支持，不得空文档标记 ready。

## 3. 数据模型
所有业务表使用 UUID、tenant_id、created_at、updated_at，并设置租户复合索引和外键约束。

### knowledge_bases
id, tenant_id, name, description, status(active/archived), settings_json, created_by, timestamps。名称唯一性按 tenant_id 范围处理。

### knowledge_documents
id, tenant_id, knowledge_base_id, source_type(file/url/text), title, original_filename, source_url, mime_type, byte_size, content_sha256, status, active_version_id, last_error_code/message, created_by, timestamps。状态包含 queued/processing/ready/failed/deleting/deleted。

### knowledge_document_versions
id, tenant_id, document_id, version_number, object_key, extracted_text_object_key, content_sha256, parser_name/version, chunking_profile, embedding_provider/model/dimension, status, created_at。document_id + version_number 唯一。

### knowledge_chunks
id, tenant_id, knowledge_base_id, document_id, document_version_id, chunk_index, content, content_sha256, token_count_estimate, metadata_json, page_number, heading_path, source_locator, search_vector。document_version_id + chunk_index 唯一。

### knowledge_embeddings
id, tenant_id, chunk_id, embedding_provider, embedding_model, embedding_dimension, vector, created_at。chunk_id + embedding_model 唯一；必须保证维度与模型匹配。若多维度单表无法安全建索引，则按模型/维度分表或分区。

### knowledge_base_personas
tenant_id, knowledge_base_id, persona_id, enabled, retrieval_config_json。复合唯一键；关联时校验 KB 与 Persona 属于同一租户。

### knowledge_ingestion_jobs
id, tenant_id, knowledge_base_id, document_id, document_version_id, job_type, status, attempt_count, max_attempts, idempotency_key, progress_percent, error_code/message, started_at, finished_at, created_at。状态 queued/running/retrying/succeeded/failed/cancelled；唯一幂等键阻止重复入队。

### knowledge_usage_events
id, tenant_id, job_id, document_id, resource_code(parse_pages/storage_bytes/embedding_input_tokens/embedding_requests), quantity, provider/model, status, occurred_at, metadata_json。只记录实际资源，不生成推测 Token 或金额；Phase 5D 再制定收费 SKU。

## 4. 导入与处理流水线
状态：queued → validating → extracting → normalizing → chunking → embedding → indexing → ready。失败进入 retrying，超过最大次数进入 failed。Worker 必须支持有限重试、超时、幂等和失败任务查询。

### 文件上传
- multipart 上传；校验扩展名、MIME、文件魔数、大小、租户配额。首版默认单文件上限 50 MB，可配置。
- 限制解压后大小、页数、行数、解析耗时；防 ZIP bomb、路径穿越、宏执行；绝不执行上传内容。
- 使用随机 object key，原文件名仅作为元数据。同一 KB 内相同内容哈希去重，避免重复 Embedding。

### URL 导入
- 仅 HTTP/HTTPS；禁止 localhost、环回、RFC1918、链路本地、保留地址和云元数据 IP。
- DNS 解析后校验 IP；每次重定向重新校验；防 DNS rebinding；限制重定向、响应大小、MIME 和超时。
- 禁止 file/ftp/data 等协议；不携带用户 Cookie 或登录态。保存标题、canonical URL、抓取时间和内容哈希；页面变更创建新版本。

### 解析、标准化与切片
- 解析为统一 blocks：text、heading、table、list、page break、source locator；保留页码、标题层级、表格与来源定位。
- 默认按语义段落/标题切分，初始 500–800 tokens，overlap 80–120 tokens；表格/代码块尽量保持完整。
- 超长段落按句子边界切分；不跨文档/租户切片；chunk 保存 page_number、heading_path、source_locator。
- Chunking profile 版本化；重新切片创建新 document version，不覆盖历史版本。

### Embedding 与索引
- 批量调用，限制批大小、并发、超时、指数退避和最大重试；部分失败可从失败批次恢复。
- 对内容哈希未变的成功 chunk 复用 Embedding；模型/provider/维度变化必须重建或独立索引。
- 记录真实供应商 usage（如可信）、请求数、耗时、状态；不记录密钥或全文日志。

## 5. 检索与 RAG
1. 从 GatewayPrincipal/Persona 确定 tenant_id、persona_id；请求体不能指定租户。
2. 读取当前 Persona 已关联且启用的 KB；没有关联 KB 时维持现有无 RAG 行为。
3. 使用与索引相同 embedding model 生成 query vector。
4. 查询强制过滤 tenant_id、KB 授权、文档 ready、active document version。
5. 分别执行向量 Top-K 与全文 Top-K，使用 RRF 融合、去重，并受 context token budget 硬上限约束。
6. 配置相似度阈值；低于阈值视为无证据，不强行注入。
7. 检索内容作为 untrusted reference context 与系统指令分区注入；明确要求模型将文档内容视为资料，不执行文档中的指令或工具调用要求。
8. 返回结构化 citations：document_id、version_id、chunk_id、title、source_url/page、snippet、score、source_locator。
9. 无证据时明确说明资料不足，不伪造来源。安全/租户校验异常必须 fail-closed；检索服务异常默认返回明确错误，不能静默绕过知识库。

### 网关兼容
- 保持 POST /v1/chat/completions 既有 OpenAI 请求格式。
- Persona 配置 RAG 开关、KB 关联、top_k、similarity_threshold、max_context_tokens、retrieval_mode；参数设硬上限。
- 非流式响应可增加 citations 扩展字段；SSE 定义兼容的 metadata/citations chunk 并写明客户端解析规范，不能破坏标准流式消费。
- usage 区分 Chat 模型 Token 与 Embedding Token；最终 Chat prompt_tokens 以供应商可信 usage 为准。

## 6. API
- POST/GET /knowledge-bases；GET/PATCH/DELETE /knowledge-bases/{kb_id}
- POST /knowledge-bases/{kb_id}/documents（multipart）及 POST /knowledge-bases/{kb_id}/urls
- GET /knowledge-bases/{kb_id}/documents；GET/DELETE /knowledge-bases/{kb_id}/documents/{document_id}
- POST /knowledge-bases/{kb_id}/documents/{document_id}/reprocess
- GET /knowledge-jobs/{job_id}
- POST /knowledge-bases/{kb_id}/search（检索调试）
- GET/PUT /personas/{persona_id}/knowledge-bases（查询/替换关联）
所有租户身份由 CurrentPrincipal 注入；分页、排序白名单、过滤上限；不返回 object key、API Key 或内部凭据。

## 7. 控制台
- 知识库列表：名称、文档数、已索引数、失败数、存储量、更新时间。
- 创建/编辑知识库；上传文件、添加 URL；显示任务状态、进度、错误和重试入口。
- 文档详情：版本历史、解析摘要、Chunk 预览、来源元数据。
- 检索测试台：问题输入、Top-K 片段、分数、来源、融合排名和安全参数。
- Persona 编辑页：多选关联知识库、启停 RAG、配置 top_k/context budget。
- 用量页分别展示解析页数、Embedding 请求/Token、存储字节；不展示虚构金额。

## 8. 安全与隐私
- API、Worker、检索 SQL 全部强制 tenant_id；建议 PostgreSQL RLS 作为纵深防御，并以跨租户自动化测试作为发布阻断条件。
- 私有对象存储；下载先验证 tenant ownership，再签发短时 URL 或由 API 流式返回。
- SSRF 防护包含 DNS 解析、IP 范围、重定向二次检查、响应体限制和 DNS rebinding 防护。
- 文档内容作为不可信输入，防 Prompt Injection；不执行文档中的命令、代码或工具指令。
- 日志不得记录全文、Authorization、API Key、签名 URL 或敏感个人数据。
- 删除必须使文档、版本、chunks、embeddings、检索缓存和下载入口不可再访问；物理清理任务幂等可恢复。

## 9. 测试验收
功能：PDF/DOCX/TXT/Markdown/HTML/CSV 与 URL 导入；解析/切片/Embedding/索引；失败重试幂等；新版本原子切换；删除后不再检索；Persona 关联限制；Hybrid 检索语义与关键词均命中；引用定位准确；无结果不伪造引用。

隔离与安全：租户 A 无法读取/修改/删除/检索租户 B 的 KB、文档、任务和 chunks；跨租户 Persona 关联拒绝；SSRF 覆盖 localhost、RFC1918、IPv6 local、metadata、重定向内网、DNS rebinding；文件覆盖超限、伪造 MIME、ZIP bomb、解析超时、空文本；文档 Prompt Injection 不改变系统规则。

性能与运维：1000 文档级别基准，记录 P50/P95、Recall@K、MRR；Worker 健康检查、任务超时、有限重试和失败任务查询；Docker smoke 覆盖 API、Worker、Redis、Postgres/pgvector、MinIO；迁移可在 Phase 5C 数据库执行；提供备份恢复和对象清理策略。

## 10. 实施拆分
- 6.0：代码审计、ADR、模型/迁移设计、Embedding/解析依赖评估、Worker/Docker 方案。
- 6.1：KB/Document CRUD、对象存储、文件上传、URL 安全导入、任务状态。
- 6.2：解析器、标准化 blocks、版本、chunking、Embedding Provider、pgvector/FTS 索引。
- 6.3：Hybrid Retrieval、RRF、Persona 关联、Gateway 上下文注入与 citations。
- 6.4：控制台知识库 UI、检索测试台、用量/失败处理。
- 6.5：租户隔离、安全测试、性能基准、迁移/备份恢复、部署验收和阶段报告。

## 11. 交付原则
- 每个子阶段独立迁移、测试和验收；不做不可回滚的大迁移。
- 不破坏 Phase 5B 平台模型/BYOK 路由、API Key、Persona、Phase 5C 计量。
- 不支持的格式/OCR 能力必须明确标注，不能在 UI 显示已完成。
- Phase 5D 支付、真实扣费、充值、退款和发票最后实施；Phase 6 不以支付接入为前置条件。