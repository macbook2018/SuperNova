# ADR 0005：多租户知识库摄取与 RAG 架构

- 状态：Accepted
- 日期：2026-11-12
- 适用阶段：Phase 6

## 背景

当前系统已经有知识库元数据、Persona 关联、PostgreSQL/pgvector 和 S3 兼容对象存储，但还没有文档生命周期、Embedding、检索和 RAG。知识库是客服与 Agent 产品的关键能力，不能只做一个上传页面或把文件正文直接拼到提示词中。

## 决策

1. 原始文件存储在 S3 兼容对象存储；PostgreSQL 保存租户隔离的元数据、任务、chunks、向量和用量记录。
2. 文档摄取由独立 Worker 执行，任务持久化在 PostgreSQL，使用行锁、租约和重试；不依赖 FastAPI 进程内 BackgroundTasks。
3. 文档通过统一 parser adapter 转换为带来源元数据的结构化文本，再按知识库配置切片和 Embedding。
4. Embedding 模型与 Chat 模型能力分开建模；支持平台托管和 BYOK，不假设每个 Chat 模型都支持 /embeddings。
5. 每个知识库的活动索引固定模型、维度和版本。重建索引先写入新版本，完成后原子切换，避免新索引失败破坏旧索引。
6. 检索使用 pgvector 语义召回与 PostgreSQL 词法召回，再用 Reciprocal Rank Fusion 合并；Reranker 是可选 adapter。
7. RAG 只读取 Persona 明确关联、同一 tenant 拥有且处于 ready 状态的知识库。
8. 检索内容以不可信引用上下文注入系统提示词；回答引用通过 `supernova.citations` 扩展返回，绝不伪造来源。
9. Phase 6 只采集存储、Embedding 和检索用量；不执行任何真实收费。商业化支付仍在 Phase 5D 最后实施。

## 后果

- API、Worker 可以独立扩缩容和重启；任务状态可观测且可恢复。
- 原始文件不进入数据库，文档删除可以独立执行对象存储清理。
- 平台 Embedding 模型需要独立目录与凭据管理；BYOK Embedding 可复用现有加密 ModelProvider。
- 中文词法召回使用 trigram/字符级策略，不依赖 PostgreSQL 默认英语词干分析。
- pgvector 先以明确的 embedding dimension 和索引版本约束向量空间；模型变更必须重建，不允许混合维度。
- RAG 引用、文档页码和表格位置成为 API 契约，前端和下游渠道可以复用。
