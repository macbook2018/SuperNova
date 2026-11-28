# 知识库索引版本与恢复

Phase 6.5A 在原有知识库上增加可选的整库候选索引和单文档恢复。数据库迁移为 `0015_rag_index_releases`。升级不改写原有 `vector(1536)` 或 Persona 绑定，不自动触发模型调用。用户级 Key、普通与流式聊天仍走原网关。本文的管理接口使用知识库所在工作空间 owner 的 Console 登录令牌。

## 查看文档正在使用的切片

在控制台进入**知识库 → 管理知识库**，在已就绪文档旁选择**查看切片**。预览每页显示 10 条正文，可以展开同一切片涉及的多个原文位置。结构化版本可显示标题路径、页码、行号或单元格。旧索引仅展示入库时实际保存的位置。

工作空间成员也可以调用 `GET /knowledge-bases/{kb}/documents/{doc}/chunks`。首请求可指定 `limit`（1 至 50，默认 10）。响应含在线发布 ID、文档索引版本、`items`、`has_more` 和 `snapshot`。后续页传入相同的 `snapshot` 和新的 `offset`。发布、回退或文档重建导致版本变化时，接口返回 409，在控制台选择**重新读取首页**即可。预览直接读取索引正文，浏览时不会调用模型。

## 在测试知识库验证一次整库发布

1. 上传文档并等到所有文档 ready。确认单库 `/search` 能命中一段可辨认的原文，记录其文档和切片 ID。保存一次 Persona 普通响应与流式最后一帧的引用。
2. 读取 `GET /knowledge-bases/{kb}/index-versions`，记录 `generation` 与 `active_release_id`。初始代次为 0，活动发布为空表示读取 v1。
3. 新策略 `structured_v1` 会保留层级标题、表头、原件位置，并在同一节内合并短段。未指定新策略时使用 `legacy`。PDF 需要文本层。以相同原件建立两组候选可以比较切片效果。用同一路径 POST 创建候选，携带如下请求。尺寸和重叠按字符计算。更换配置时使用新的请求键；同键同参数可以安全重试。服务会冻结该库所用 Embedding 配置的身份和 1536 维约束。模型供应商密钥仍由现有平台池或租户 BYOK 在执行时解析。

   ```json
   {
     "request_key": "hk-docs-trial-01",
     "expected_generation": 0,
     "config": {"parser_version": "structured_v1", "chunk_size": 600, "chunk_overlap": 80}
   }
   ```

4. 从响应取候选 ID，轮询 `GET /knowledge-bases/{kb}/index-versions/{release}` 至 ready。核对在线搜索仍返回原切片；失败的候选不会改变活动版本。候选会重读 Silo 原件并计算新的 Embedding，用量按实际外部请求观测。
5. 调用 `POST /knowledge-bases/{kb}/index-versions/{release}/publish`，请求体为 `{"expected_generation":0}`（以实际最新值为准）。再测原问句和两种聊天模式，新引用应指向已发布切片。到 `GET /knowledge-bases/{kb}/usage` 检查活动切片和调用统计；步骤级观测和原有兼容汇总反映的调用可能相同，两者不能直接相加。
6. 调用 `POST /knowledge-bases/{kb}/index-versions/rollback`，提交 `{"expected_generation":1,"target_release_id":null}`（同样使用最新代次），再次检索并核对原 v1 切片 ID。也可指定此前已发布且仍与完整文档清单相符的发布 ID。收到 409 时重新读取活动状态，确认目标是否已生效，避免以旧代次覆盖他人操作。

候选范围是整个知识库，同库文档必须 ready、原件哈希已知且当前无摄取作业。**实际上传新文档、发布新 FAQ、删除或按原接口重建等资料变动，会使旧候选失效，服务原子切回持续维护的 v1。** 此时需要按新代次重新构建并发布。重复上传同一份资料不会单独触发切换。停用/撤销知识库授权立即影响检索。恢复历史也会检查完整文档清单和模型身份。

## 恢复按原接口重建的单份文档

1. 在 `GET /knowledge-bases/{kb}/documents/{doc}/index-history` 查看活动文档版本、库代次及可用修订。已有库在升级后的首次重建时才开始登记历史；升级前已被删除的切片无法重新生成原 ID。
2. 调用 `POST /knowledge-bases/{kb}/documents/{doc}/index-history/{version}/restore`，传入 `expected_active_index_version` 和 `expected_generation`。服务会核对租户、原件哈希、整套切片、作业状态和当前读模型的指纹。成功后检索直接复用原切片 ID。下一次重建继续分配更大的版本号。
3. `profile_origin=generated` 代表 Worker 记录了当时实际使用的模型，`legacy_unknown` 表示升级以前的向量，只能验证在首次替换时观察到的读取配置。显式删除原文档会同时清理其历史内容。普通重建保留多个版本，试点前应预留原件和多套向量所需的空间。

## 香港候选验收

使用与 PR 一致的不可变 SHA、独立数据库/卷和 Silo 对象范围，记录应用 SHA、`alembic current`、pgvector 版本、模型标识和实际返回向量长度。用同一份脱敏客服资料执行上述两种恢复，核对另一个租户不能查看版本、恢复或检索；再测候选构建时写入源文档以及供应商失败。抽查新的 API、旧 Worker/旧镜像写入后升级的可见性。采集请求 ID、来源 ID 和用量，无需记录密钥。

运维回退优先切读指针，保留增量数据库表。旧应用部署前应排空 Worker 并记录活动发布。旧写者可能清理它自己的 v1 历史切片，新版对不完整版本拒绝恢复。已有 v2 状态或调用观测时，迁移拒绝可能丢数据的 schema downgrade。GitHub 的 Compose 测试使用独立 Silo、真实 PostgreSQL 和本地模型协议桩；真实 Qwen3-Embedding-8B 的外呼、实际消耗与检索效果由香港候选环境另行验证。
