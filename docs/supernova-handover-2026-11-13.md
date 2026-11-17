# SuperNova 开发交接报告

- **报告日期：** 2026-11-13
- **仓库：** https://github.com/macbook2018/SuperNova
- **开发分支：** \`feat/phase-6-knowledge-base-rag\`
- **报告基准 Commit：** \`9e3db54a6a5ca195d36a3a0af5d40bb14e422e10\`
- **关联 PR：** [#10 — Phase 6: complete knowledge ingestion, hybrid RAG and Persona citations](https://github.com/macbook2018/SuperNova/pull/10)
- **报告口径：** 以用户提供的终端输出、截至报告日期 GitHub 分支代码及 PR 状态为依据。没有实际执行或无法从记录确认的事项均标记为“未验证”或“待处理”。

---

## 1. 当前开发阶段及完成情况

### 1.1 Phase 6：知识库与 RAG

当前开发分支包含 Phase 6 的知识库、文档摄取、Embedding、混合检索及 RAG 网关相关代码，涉及：

- 租户隔离的知识库和文档 API。
- 文件上传、公开 URL 导入及 S3 兼容私有对象存储。
- 独立知识库 Worker、持久化任务、租约和重试机制。
- PDF、DOCX、PPTX、XLSX、TXT、Markdown、CSV、HTML 等解析能力。
- 结构化切片和 1536 维 Embedding。
- pgvector 向量检索、pg_trgm 词法检索及 Reciprocal Rank Fusion（RRF）。
- Persona 关联知识库后的 RAG 上下文注入及引用返回。
- 控制台知识库管理和检索调试相关代码。

**状态：代码已进入当前分支；不能认定 Phase 6 已完成验收。** 仓库路线文档仍将 6A/6B/6C 标记为基础代码落地阶段，最终状态依赖 CI、数据库迁移及端到端验收。PR #10 仍为 Draft、Open，尚未合并。

### 1.2 Phase 6.1A：聊天文本导入基础

本轮重点是将聊天导入从“聊天记录存储/浏览”调整为“人格设定和知识库的临时分析素材”。

当前分支代码已实现：

- 创建和管理独立的聊天来源账号。
- 服务端生成 Source Account ID。
- 上传 JSON、CSV、TXT、HTML、XLSX、ZIP 格式导出文件。
- 解析文本消息，统计发送者及文本消息数量。
- 图片、语音、视频等非文本消息计数后跳过。
- 用户手动确认哪个发送者代表当前登录账号本人。
- 保存导入任务状态、文件元数据及临时对象存储信息。
- 本人身份确认后，任务进入 \`ready_for_analysis\`。
- 控制台展示来源账号、导入任务、发送者摘要及本人身份确认，不再提供聊天正文浏览。

**尚未完成：**

- Persona 自动提炼服务。
- Knowledge/FAQ 候选生成服务。
- Persona 草稿审核、应用和回滚界面。
- Knowledge 候选编辑、审核、批准及写入现有 RAG 知识库的完整流程。
- 成功分析后的临时原文件清理闭环。
- 最新代码版本的本机全量测试及端到端验收。

因此，当前准确状态是：**文本导入和本人身份确认基础已提交；AI 提炼与审核闭环尚未实现。**

---

## 2. 当前 Git 分支及最新 Commit

用户本机已执行：

- \`git switch feat/phase-6-knowledge-base-rag\`
- \`git pull --ff-only origin feat/phase-6-knowledge-base-rag\`
- \`git log -1 --oneline\`

终端确认：

| 项目 | 值 |
|---|---|
| 当前分支 | \`feat/phase-6-knowledge-base-rag\` |
| 最新短 Commit | \`9e3db54\` |
| 完整 Commit | \`9e3db54a6a5ca195d36a3a0af5d40bb14e422e10\` |
| Commit 信息 | \`fix(console): keep source accounts visible during cleanup\` |
| 本次 Fast-forward 起点 | \`10a275e\` |
| 本次同步提交数 | 33 |
| PR | #10 |
| PR 状态 | Open、Draft、未合并 |

用户终端显示本次同步为 Fast-forward，未发生合并冲突。

\`git stash pop\` 返回 \`No stash entries found\`，说明当时没有可恢复的 stash。**最新 pull 后的 \`git status --short\` 没有提供，因此当前工作区是否完全干净尚未确认。**

此前曾出现未跟踪文件 \`apps/console/package-lock.json\`；最新状态需要重新检查，不能假设它已经消失。

---

## 3. 已修改和新增的文件

### 3.1 本次从 10a275e 同步到 9e3db54 的文件

GitHub 比较结果为 **15 个已有文件修改，无新增文件、无删除文件**：

1. \`.env.example\`
2. \`apps/api/app/api/chat_import.py\`
3. \`apps/api/app/chat_import/parsers.py\`
4. \`apps/api/app/chat_import/worker.py\`
5. \`apps/api/app/core/config.py\`
6. \`apps/api/app/models/__init__.py\`
7. \`apps/api/app/models/chat_import.py\`
8. \`apps/api/app/schemas/chat_import.py\`
9. \`apps/api/migrations/versions/0009_chat_import.py\`
10. \`apps/api/tests/test_chat_import.py\`
11. \`apps/api/tests/test_chat_import_parsers.py\`
12. \`apps/console/src/App.vue\`
13. \`apps/console/src/components/ChatImportsView.vue\`
14. \`apps/console/src/lib/api.ts\`
15. \`docs/phase-6-chat-import-design.md\`

### 3.2 PR #10 相对 Phase 5C 基线的完整文件清单

PR #10 当前共涉及 **47 个文件：29 个新增、18 个修改**。

#### 新增文件（29）

**API / Chat Import**
- \`apps/api/app/api/chat_import.py\`
- \`apps/api/app/chat_import/__init__.py\`
- \`apps/api/app/chat_import/parsers.py\`
- \`apps/api/app/chat_import/worker.py\`
- \`apps/api/app/models/chat_import.py\`
- \`apps/api/app/schemas/chat_import.py\`

**API / Knowledge Base 与 RAG**
- \`apps/api/app/api/embedding_models.py\`
- \`apps/api/app/api/knowledge.py\`
- \`apps/api/app/knowledge/__init__.py\`
- \`apps/api/app/knowledge/chunking.py\`
- \`apps/api/app/knowledge/embeddings.py\`
- \`apps/api/app/knowledge/network.py\`
- \`apps/api/app/knowledge/parsers.py\`
- \`apps/api/app/knowledge/retrieval.py\`
- \`apps/api/app/knowledge/storage.py\`
- \`apps/api/app/knowledge/worker.py\`
- \`apps/api/app/models/knowledge.py\`
- \`apps/api/app/schemas/knowledge.py\`

**数据库迁移**
- \`apps/api/migrations/versions/0008_phase_6_knowledge.py\`
- \`apps/api/migrations/versions/0009_chat_import.py\`

**测试**
- \`apps/api/tests/test_chat_import.py\`
- \`apps/api/tests/test_chat_import_parsers.py\`
- \`apps/api/tests/test_knowledge.py\`
- \`apps/api/tests/test_knowledge_embeddings.py\`
- \`apps/api/tests/test_knowledge_retrieval.py\`

**Console / 文档**
- \`apps/console/src/components/ChatImportsView.vue\`
- \`docs/decisions/0005-knowledge-ingestion-rag.md\`
- \`docs/phase-6-chat-import-design.md\`
- \`docs/roadmap/phase-6-knowledge-base-rag.md\`

#### 修改文件（18）

- \`.env.example\`
- \`apps/api/app/api/catalog.py\`
- \`apps/api/app/api/gateway.py\`
- \`apps/api/app/billing/credentials.py\`
- \`apps/api/app/billing/metering.py\`
- \`apps/api/app/core/config.py\`
- \`apps/api/app/main.py\`
- \`apps/api/app/models/__init__.py\`
- \`apps/api/app/models/catalog.py\`
- \`apps/api/app/schemas/catalog.py\`
- \`apps/api/migrations/env.py\`
- \`apps/api/pyproject.toml\`
- \`apps/api/tests/test_metering_postgres.py\`
- \`apps/console/src/App.vue\`
- \`apps/console/src/lib/api.ts\`
- \`apps/console/src/styles.css\`
- \`deploy/docker/compose.yaml\`
- \`docs/roadmap/phase-5-8-commercial-rollout.md\`

---

## 4. 数据库迁移版本

### 4.1 已执行记录

用户之前实际执行并提供了以下输出：

| 命令/检查 | 结果 |
|---|---|
| Alembic 初始 current | \`0008_phase_6_knowledge\` |
| Alembic heads | \`0009_chat_import (head)\` |
| \`alembic upgrade head\` | 命令执行完成，无报错输出 |
| upgrade 后 current | \`0009_chat_import\` |

因此，**最后一次实际观察到的 Alembic 版本是 \`0009_chat_import\`**。这次最新 Git pull 后没有再次执行 current 或检查数据库物理结构。

### 4.2 必须优先处理的迁移兼容风险

检查 GitHub 历史代码发现：\`0009_chat_import.py\` 在本次 33 个提交中被修改过，但 Revision ID 仍然是 \`0009_chat_import\`。

旧版 0009（10a275e）创建的聊天表：

- \`chat_sources\`
- \`chat_import_jobs\`
- \`chat_import_files\`
- \`chat_conversations\`
- \`chat_participants\`
- \`chat_messages\`

当前 9e3db54 版本的 0009 创建的表：

- \`chat_sources\`
- \`chat_import_jobs\`
- \`chat_import_files\`
- \`chat_import_sender_summaries\`

当前 ORM 模型也只定义上述四张 Chat Import 表，不再定义 conversations、participants、messages。

**风险判断：高，尚未通过本机数据库查询最终确认。**

由于用户此前已经将数据库升级并记录为 \`0009_chat_import\`，如果当时执行的是旧版 0009，那么数据库中的 Alembic 版本号仍然是 0009，但物理表结构不会因为 Git pull 修改迁移文件而自动改变。Alembic 也不会重新执行已经记录为完成的同一 revision。

因此可能出现：

- 数据库仍有旧的 \`chat_conversations\`、\`chat_participants\`、\`chat_messages\` 表。
- 数据库缺少新版代码需要的 \`chat_import_sender_summaries\` 表。
- ORM、API、Worker 与数据库结构不一致。

**处理要求：** 下一步先核验数据库结构，再新增后续迁移（建议 0010）兼容旧库：清理不再需要的聊天归档表，并确保 sender summaries 表及约束存在。迁移前先备份数据库。不能仅凭 \`alembic current\` 显示 0009 就判断数据库结构正确，也不要指望再次执行 \`upgrade head\` 自动重跑修改过的 0009。

建议先执行只读核验：

    python -m alembic -c apps/api/alembic.ini current

    SELECT version_num FROM alembic_version;

    SELECT tablename
    FROM pg_tables
    WHERE schemaname = 'public'
      AND tablename LIKE 'chat_%'
    ORDER BY tablename;

---

## 5. 已执行的测试及完整结果

本节严格区分历史版本结果与最新 Commit 结果。

### 5.1 历史本机 API 测试

在较早的代码版本（Commit \`730c551e9e9275134baffa7b636a07caa1cdcfac\`）用户实际执行过：

**Chat Import 定向测试**

    python -m pytest -c apps/api/pyproject.toml apps/api/tests/test_chat_import.py -v

结果：

- 5 passed
- 1 warning
- 用时 1.56s

**API 全量测试**

    python -m pytest -c apps/api/pyproject.toml apps/api/tests

结果：

- 93 passed
- 1 skipped
- 4 warnings
- 用时 6.89s

随后执行：

    python -m pytest -c apps/api/pyproject.toml apps/api/tests -rs

结果：

- 93 passed
- 1 skipped
- 4 warnings
- 用时 7.04s

跳过原因：

- \`apps/api/tests/test_metering_postgres.py:61\`
- PostgreSQL concurrency integration test requires \`SUPERNOVA_DATABASE_URL\`

警告包括 Starlette TestClient 与 httpx 的弃用提示，以及 \`HTTP_422_UNPROCESSABLE_ENTITY\` 弃用提示。

**重要限制：以上测试发生在较早 Commit，不代表 9e3db54 已通过全量测试。**

### 5.2 历史失败及修复过程

开发过程中曾出现：

1. 全量测试 5 failed、88 passed、1 skipped。主要表现为重复导入请求返回 409。
2. 后续定向测试仍有 2 failed、3 passed，错误涉及 SQLAlchemy StatementError 和 UUID 参数类型。
3. 后续提交调整测试和实现后，定向测试 5 passed，全量测试达到 93 passed、1 skipped。

这些是历史调试结果，不应当作为最新代码的失败状态，也不能替代最新代码的重新测试。

### 5.3 Ruff 检查

历史记录中：

- 对部分文件执行过 Ruff 自动修复，结果显示 All checks passed。
- 后续全量 Ruff 检查曾发现 1 个 \`UP012\` 问题，位置为当时版本的 \`apps/api/tests/test_chat_import.py:73\`。
- 没有提供 9e3db54 对应的全量 Ruff 最终通过结果。

**最新 Ruff 状态：未验证。**

### 5.4 Console 构建

用户曾执行：

    docker compose -f deploy/docker/compose.yaml exec console npm run build

当时结果：

- \`vue-tsc --noEmit\` 通过。
- Vite 6.0.7 构建成功。
- 1565 modules transformed。
- CSS：37.92 kB，gzip 8.29 kB。
- JS：176.95 kB，gzip 61.10 kB。
- 构建用时 3.19s。

该结果发生在最终文本导入页面重构之前，**不能视为最新 Console 代码已构建通过**。

### 5.5 Docker Worker

用户曾执行：

- 构建并启动 \`chat-import-worker\`。
- 日志出现 \`chat_import_worker_started\`。
- 后续 \`docker compose ps chat-import-worker\` 显示 \`Up ... (healthy)\`。

这是此前代码版本的 Worker 启动验证。最新 Git pull 后是否已重新构建并运行，尚未验证。

此前 API、Console、MinIO、PostgreSQL、Redis 容器也曾显示 healthy；该状态不代表最新代码已经部署到容器。

### 5.6 最新 Commit 的 CI

GitHub Actions 对最新 Commit \`9e3db54\` 的 Run \`43391241693\` 显示三个 Job 均失败：

- Docker Compose build and smoke test
- Console typecheck and production build
- API lint, tests and migrations

检查 Job 明细后，三个 Job 均为 **0 steps**，没有执行步骤和可用测试日志。

因此：

- CI 当前状态是 failure。
- 但该结果没有实际执行构建或测试步骤，不能据此认定代码测试失败。
- 同样不能将其解释为 CI 已通过。

### 5.7 最新 Commit 本机测试

用户同步到 9e3db54 后执行：

    python -m pytest -c apps/api/pyproject.toml apps/api/tests

终端返回：

    zsh: command not found: python

原因是当前终端提示符没有显示 \`(.venv)\`，Python 虚拟环境未激活。用户此前已成功执行过 \`source .venv/bin/activate\`。

**最新 Commit 9e3db54 的全量 pytest、Ruff、Console build、Alembic 迁移和 Docker 重建均未完成验证。**

---

## 6. 尚未解决的问题

按风险和优先级排列：

### P0：数据库迁移兼容性

- 0009 迁移文件在同一 Revision ID 下被改写。
- 用户数据库此前已经执行过 0009。
- 旧版归档表与新版 sender summaries 表存在结构差异。
- 尚未检查用户当前 PostgreSQL 的真实表结构。
- 需要新增兼容迁移，不能直接假设现有数据库已符合新模型。

### P1：最新代码验证缺失

- 最新 HEAD 的全量 pytest 未执行。
- 最新 HEAD 的 Ruff 未执行。
- 最新 Console 构建未执行。
- 最新 Docker 镜像尚未确认重建。
- GitHub Actions 三个 Job 均在 0 steps 处失败。

### P1：Persona 与 Knowledge 自动提炼尚未实现

目前导入任务停留在 \`ready_for_analysis\`。尚未完成：

- 重新读取临时上传文件。
- 按已确认的本人身份筛选本人消息。
- Persona 结构化提炼。
- FAQ 和业务知识候选提炼。
- 人工审核、编辑、批准和拒绝。
- 将批准的知识候选接入现有 Knowledge Base 文档摄取流程。

### P1：成功分析后的临时文件清理闭环

当前已经有取消、失败和过期清理相关实现；但 Persona/Knowledge 分析服务还未完成，因此成功分析后的原始文件删除流程尚未验证。

### P2：账号与外部连接关系

- 当前 Source Account ID 是 SuperNova 创建的内部来源账号标识。
- 用户需要为不同登录微信账号分别创建、选择来源账号。
- 尚未实现第三方微信客户端连接凭证与 Source Account ID 的自动绑定。
- 不能仅凭 wxid、昵称或群 ID 推断第三方登录的是哪个微信账号。

### P2：本地工作区状态

- 最新 pull 后未提供 \`git status --short\`。
- 之前曾出现未跟踪的 \`apps/console/package-lock.json\`。
- 当前是否存在本地未提交修改或未跟踪文件尚未确认。

---

## 7. 已确认的技术决策

### 7.1 聊天导入的产品目的

聊天记录导入只用于：

1. 提炼人格设定（Persona）。
2. 提炼业务知识和 FAQ（Knowledge Base）。

**不建设聊天记录存储、聊天记录浏览、会话搜索或消息回放功能。**

### 7.2 文本优先

- 当前只处理文本消息。
- 图片、语音、视频、文件、表情等非文本消息跳过。
- 不在本阶段增加 OCR、语音转写或多模态分析。

### 7.3 Source Account 隔离

采用方案 A：完全独立。

- 每个登录微信账号对应一个独立 Source Account ID。
- Source Account ID 由 SuperNova 服务端生成。
- 同一租户下不同 Source Account 的联系人、群聊、标签、备注、消息来源及画像不得合并。
- 不根据 wxid、群 ID、昵称、备注或标签进行跨账号匹配。
- 所有导入数据按 tenant_id + source_account_id 隔离。

### 7.4 本人身份确认

- 系统不根据消息数量、昵称或 wxid 自动推断本人。
- 用户手动选择代表当前登录微信账号本人的发送者。
- Persona 只使用本人发送的消息。
- 其他参与者的消息可以作为问题和上下文，但不能自动视为权威业务知识。
- 本人回复是知识候选的主要事实来源。

### 7.5 提炼结果审核

- Persona 先生成草稿，不自动覆盖已有 System Prompt。
- Knowledge 先生成待审核候选。
- 只有用户批准的候选才能写入现有 Knowledge Base 并进入 RAG 索引。
- 对冲突、过期或带有时间属性的知识不能静默合并。

### 7.6 临时文件与隐私

- 原始上传文件保存在私有对象存储。
- 默认临时保留 24 小时。
- TTL 配置范围为 1～72 小时。
- 不把聊天正文写入 PostgreSQL 或普通日志。
- 发送者摘要只用于本人身份确认，确认后删除。
- 过期任务需要重新上传，不以持久化聊天正文支持重试。
- 导入内容必须作为不可信输入处理，不能执行其中包含的指令。

### 7.7 商业化和计费边界

- 模型使用模式：默认平台模型，同时允许用户 BYOK。
- 收入模式：订阅费 + 超额资源收费。
- Phase 6 的知识库用量仅作运营统计或 shadow 计量。
- 当前阶段不接支付、不扣钱包余额、不执行真实超额收费；支付及真实结算留到 Phase 5D。

---

## 8. 下一阶段具体任务

建议按以下顺序执行，不要跳过数据库兼容处理。

### Task 1：完成数据库 0010 兼容迁移

1. 查询当前数据库 Alembic revision。
2. 查询所有 \`chat_%\` 表。
3. 备份开发数据库。
4. 新增 0010 迁移，兼容已经应用旧版 0009 的数据库。
5. 删除不再需要的 conversations、participants、messages 归档表。
6. 创建新版 \`chat_import_sender_summaries\` 表及其索引、外键和约束。
7. 验证全新数据库从 0008 升级，以及旧数据库从 0009 升级，两条路径均正确。
8. 验证 ORM metadata 与 PostgreSQL 实际 schema 一致。

### Task 2：恢复本机开发与测试环境

1. 激活 Python 虚拟环境。
2. 确认 Python、Ruff、pytest 版本和依赖。
3. 运行 Ruff 全量检查。
4. 运行 API 全量 pytest。
5. 配置测试 PostgreSQL 的 \`SUPERNOVA_DATABASE_URL\`，执行并验证 PostgreSQL 并发测试。
6. 运行 Alembic upgrade/current。
7. 执行 Console typecheck 和 production build。

### Task 3：实现 Persona 自动提炼

1. 接收 \`ready_for_analysis\` 任务。
2. 从私有临时对象重新解析文本。
3. 仅提取本人发送的文本。
4. 提炼语言、语气、正式程度、句式长度、表达习惯、常见回复模式等可观察特征。
5. 不推断敏感属性或未经证实的心理特征。
6. 生成结构化 Persona Draft。
7. 增加草稿预览、编辑、批准、拒绝。
8. 用户批准后才更新 Persona；保留现有配置的回滚能力。

### Task 4：实现 Knowledge/FAQ 自动提炼

1. 将客户问题与本人回复建立候选问答关系。
2. 提取产品信息、价格、规格、业务政策、操作流程和已验证解决方案。
3. 其他参与者的内容只作为问题上下文。
4. 对时间敏感或互相冲突的答案保留不同候选。
5. 生成带标题、类别、答案、问题变体、置信度和可选生效日期的候选记录。
6. 所有候选默认 \`pending_review\`。
7. 增加编辑、批准、拒绝功能。
8. 批准后转换为现有 Knowledge Base 文档，复用已有解析、切片和 Embedding 流程。

### Task 5：完成临时数据清理

- 成功分析后删除原始上传对象。
- 取消、失败、过期时删除原始上传对象。
- 清理临时文件元数据。
- 清理未审核任务对应的草稿和候选。
- 不删除用户已经明确批准并应用的 Persona 或已发布知识文档。
- 验证重复执行清理任务不会导致异常。

### Task 6：Console 审核界面

只增加以下页面能力：

- Persona 草稿预览、编辑、批准、拒绝。
- Knowledge 候选列表、编辑、批准、拒绝。
- 显示提炼任务状态和错误摘要。
- 显示批准后的 Persona 和 Knowledge Base 关联入口。

不增加聊天记录浏览器、会话列表、消息搜索或媒体画廊。

### Task 7：完整验收并合并 PR

- API 单元测试和集成测试。
- PostgreSQL 真实数据库迁移测试。
- Persona 提炼与身份隔离测试。
- Knowledge 候选来源和审核测试。
- 临时文件成功/失败/取消/过期清理测试。
- 跨租户、跨 Source Account 隔离测试。
- Console typecheck/build。
- Docker Compose API、Console、Worker 冒烟测试。
- 修复 GitHub Actions 0 steps 问题，确保 CI 实际执行步骤。
- 所有验收通过后，再将 PR #10 从 Draft 转为 Ready for review 并合并。

---

## 9. 下一阶段执行前需要注意的事项

1. **先处理 0009 迁移差异。** 在确认数据库结构并设计 0010 迁移前，不要认为 \`alembic upgrade head\` 已完成兼容。
2. **先备份数据库。** 不要直接删除 PostgreSQL 数据卷或重建数据库；如果确实选择重置，只能对确认可丢弃的开发数据库操作。
3. **激活虚拟环境。** 用户上次运行 pytest 时未激活 .venv，导致 \`zsh: command not found: python\`。执行：
   
       cd ~/Projects/SuperNova
       source .venv/bin/activate
       python -V

4. **先检查工作区。**
   
       git status --short
       git branch --show-current
       git log -1 --oneline

   不要覆盖尚未确认的本地修改或未跟踪文件。
5. **不要执行没有 stash 的恢复操作。** 上次 \`git stash pop\` 已返回 \`No stash entries found\`。
6. **Git pull 不会自动更新运行中的 Docker 镜像。** 最新代码验证前，需要按实际 Compose 服务配置重新 build 并启动 API、Console、chat-import-worker。
7. **确认 .env 配置。** 当前默认临时文件 TTL 为 24 小时；真实 .env 是否设置了对应值尚未检查。不要把真实 API Key 写入 Git。
8. **最新代码必须重新测试。** 历史 93 passed 不能替代 9e3db54 的测试结果。
9. **PostgreSQL 并发测试需要独立测试数据库 URL。** 未配置 \`SUPERNOVA_DATABASE_URL\` 时，该项会被跳过。
10. **保护聊天隐私。** 不要为了调试把聊天正文、原始导出文件或个人标识写入日志或长期数据库。
11. **PR #10 仍未合并。** 当前代码属于开发分支，不能将其描述为生产已发布版本。
12. **保持范围收敛。** 下一阶段优先完成迁移兼容、Persona 提炼、Knowledge 提炼和审核闭环；不扩展聊天存储、媒体处理或微信渠道自动化。

---

## 交接结论

截至 2026-11-13：

- GitHub 分支已同步至 \`9e3db54\`。
- Phase 6 知识库与 RAG 代码已进入 PR #10，但未完成最终验收。
- Phase 6.1A 文本导入和本人身份确认基础代码已提交。
- 当前设计明确不保存聊天正文、不构建聊天记录存储系统。
- Persona 自动提炼和 Knowledge 候选生成仍是下一阶段工作。
- **最优先的技术风险是 0009 迁移文件被原地改写，必须先核验现有数据库并制定 0010 兼容迁移。**
- 最新 Commit 尚未完成本机全量测试、Console 构建和 Docker 重建。

本报告仅记录已执行事项、可从当前代码确认的实现及明确标注的未验证事项，不将路线图或计划任务视为已完成。
