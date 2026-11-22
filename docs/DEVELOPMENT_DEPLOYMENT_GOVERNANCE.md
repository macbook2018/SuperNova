# SuperNova 统一开发与部署管理规范

版本：1.0
生效目标分支：`feat/phase-6-knowledge-base-rag`

## 1. 唯一主开发分支

所有新开发、功能迭代和 Bug 修复统一集成到：

`feat/phase-6-knowledge-base-rag`

该分支是 SuperNova 当前唯一主开发分支（Integration Branch），不是 GitHub 默认发布分支 `main`。

所有新任务 PR 必须以该分支为 Base。历史 PR #10 等已有发布/阶段 PR 单独处理，不作为新任务分支规范的例外模板。

## 2. 会话职责

| 会话 | 职责 | 分支前缀 |
|---|---|---|
| A | Phase A 功能开发 | `feat/session-a-<task>` |
| B | Phase B 功能开发 | `feat/session-b-<task>` |
| C | Bug 修复 | `fix/session-c-<bug>` |

会话标签代表责任人/工作流，不代表共享开发分支。每一个独立任务创建自己的分支，不允许两个会话共同提交到同一任务分支。

## 3. 分支规则

1. 开始任务前读取主开发分支最新 HEAD。
2. 从最新 HEAD 创建独立任务分支。
3. 禁止直接提交到主开发分支。
4. 禁止直接提交到 `main`。
5. 禁止在 Staging 服务器上修改代码或执行未授权发布。
6. 任务分支只承载一个可审查的任务范围。
7. 任务完成后创建 PR，Base 必须是 `feat/phase-6-knowledge-base-rag`。
8. PR 等待审查和 CI，不得自行合并。
9. 主开发分支更新后，未合并任务分支必须同步最新主开发分支，并重新通过 CI。
10. 禁止未经协调的 force push、回滚主开发分支或覆盖其他任务提交。

## 4. 文件冲突与共享接口

创建任务时，必须在任务说明中列出预期修改文件和共享接口。

提交 PR 前检查：

- 是否修改其他会话正在修改的文件。
- 是否改变共享 API、Pydantic Schema、数据库模型或迁移依赖。
- 是否改变 Docker Compose、环境变量或部署脚本。
- 是否引入与其他任务重复的实现。

发现重叠时，先通知中央项目管理会话，明确文件负责人和合并顺序。禁止通过覆盖文件解决冲突。

数据库迁移编号由中央项目管理会话统一分配。任务分支不得各自猜测下一个迁移编号。

## 5. PR 审核与合并门禁

PR Base 必须是：

`feat/phase-6-knowledge-base-rag`

必须通过以下 CI：

- Console typecheck and production build
- API lint, tests and migrations
- Docker Compose build and smoke test

合并前必须确认：

- PR 目标分支正确。
- 所有必需 CI 检查成功。
- PR 与最新主开发分支同步。
- 数据库迁移链无冲突。
- 共享文件变更已协调。
- 安全、租户隔离和回归测试完成。
- PR 审查意见已解决。

推荐使用 Squash Merge，保留清晰的任务级提交记录。

任何会话都不得自行合并 PR。由中央项目管理会话统一验收和合并。

合并一个 PR 后，其他未合并 PR 必须重新检查是否落后于主开发分支；必要时更新分支并重新运行 CI。

## 6. GitHub 分支保护（必须在仓库 Settings 完成）

为 `feat/phase-6-knowledge-base-rag` 创建 Branch protection rule：

- Require a pull request before merging。
- Require status checks to pass before merging。
- Required checks：
  - `Console typecheck and production build`
  - `API lint, tests and migrations`
  - `Docker Compose build and smoke test`
- Require branches to be up to date before merging。
- Require conversation resolution before merging。
- Block force pushes。
- Block branch deletion。
- 不允许绕过上述规则。

如有独立协作者，建议要求至少一名独立批准者。单人维护时，由中央项目管理会话完成审查并保留审查记录，不能依赖作者自我批准。

## 7. 唯一 Staging 部署入口

唯一部署工作流：

`.github/workflows/ci.yml` 调用 `.github/workflows/staging-deploy.yml`

部署只允许由以下 GitHub 事件触发：

- `pull_request`。
- `action == closed`。
- `pull_request.merged == true`。
- `pull_request.base.ref == feat/phase-6-knowledge-base-rag`。
- 该次合并提交上的 Console、API、Docker Compose 三组 CI 全部成功。

部署输入 SHA 使用 `pull_request.merge_commit_sha`（缺失时才回退到 `github.sha`），并在部署工作流中再次校验 checkout HEAD。

以下操作不允许触发部署：

- 功能分支 push。
- Bug 分支 push。
- 普通主开发分支 push（直接推送本身也应被 Branch protection 阻止）。
- PR opened / synchronize 等检查事件。
- 未合并的 PR close 事件。
- 手动 workflow_dispatch。
- 会话自行 SSH 登录服务器运行 deploy-staging.sh。

`STAGING_DEPLOY_ENABLED` 必须保持为显式开关。部署工作流未启用时只运行 CI，不触碰 Staging。

## 8. 部署串行锁与验收门

启用部署前，仓库管理员必须确认以下配置：

- Repository variable：`STAGING_DEPLOY_ENABLED=true`。
- Repository secrets：`STAGING_HOST`、`STAGING_USER`、`STAGING_SSH_PRIVATE_KEY`、`DOCKERHUB_USERNAME`、`DOCKERHUB_TOKEN`；`STAGING_SSH_PORT` 可选。
- Repository variables：`STAGING_DEPLOY_PATH`、`STAGING_API_BASE_URL`、`STAGING_CONSOLE_BASE_URL` 可按环境设置；后两项默认分别为 `https://api.wet001.com` 和 `https://bot.wet001.com`。
- GitHub Settings → Environments：创建 `staging-acceptance`，配置至少一名 Required reviewer。未配置 Required reviewers 时，验收 Job 会自动通过，不能形成真正的人工验收门。

Staging 部署工作流使用固定 concurrency group：

```yaml
concurrency:
  group: supernova-staging-deploy
  cancel-in-progress: false
```

该锁位于独立的可复用部署工作流层，避免普通 PR CI 相互阻塞。

锁从构建/部署开始，一直保持到部署后健康检查和 Staging acceptance 完成。GitHub Environment `staging-acceptance` 必须配置 Required reviewers；未批准时，不允许下一个部署工作流进入部署阶段。

中央项目管理会话遵守：**上一轮 Staging 验收完成前，不合并下一轮 PR。**

注意：GitHub concurrency 提供同组任务互斥，但不是无限容量的严格 FIFO 队列；同一组最多保留一个运行任务和一个待运行任务，更新的待运行任务可能替换先前待运行任务。因此必须执行“一次只合并一个待部署 PR，完成验收后再合并下一个”的管理规则。

## 9. Commit SHA 锁定

部署工作流必须：

1. 从 `workflow_call` 输入的完整 40 位 Commit SHA checkout。
2. 校验 `git rev-parse HEAD` 与目标 SHA 完全一致。
3. API 镜像使用 `api-<commit-sha>`。
4. Console 镜像使用 `console-<commit-sha>`。
5. 服务器部署脚本只接受完整 40 位 SHA。
6. 服务器验证并记录实际部署 SHA。

服务器记录：

- `.last_successful_tag`
- `.last_successful_deployment.json`

部署记录至少包含 commit_sha、image_tag、deployed_at_utc、GitHub run ID、run attempt、status 和 event_type。

不得使用 `latest` 或 `staging-latest` 作为正式部署依据。

## 10. 服务器二次互斥与失败熔断

服务器部署、回滚和健康确认脚本共享：

`.staging-deploy.lock`

使用 `flock` 防止 GitHub Actions 之外的操作与部署并发。

每次部署通过内部健康检查后，会写入：

`.staging-deployment-awaiting-acceptance`

在 GitHub Environment `staging-acceptance` 审核完成前：

- 下一次部署脚本拒绝启动。
- 普通回滚脚本拒绝在验收期间改变当前版本。
- 验收 Job 必须调用 `acknowledge-staging-health.sh --accept`，再次核验容器 SHA、服务健康和公网 API/Console，然后清除等待标记。
- 如果验收被拒绝或工作流中断，等待标记会保留，后续部署不能覆盖当前版本。

如果部署过程或部署后公网健康检查失败：

- 写入 `.staging-deployment-blocked`。
- 后续部署必须停止。
- 不得仅因下一个 GitHub Actions 任务已排队就继续发布。
- 必须检查日志、容器状态和当前 SHA。
- 可以执行经过验证的回滚；回滚成功后清除等待验收标记并解除阻断。
- 如需人工恢复，只有在所有容器镜像 SHA、PostgreSQL/Redis 与应用容器健康状态、API readiness、Console health、公网 API/Console 和部署记录均一致时，才可执行 `acknowledge-staging-health.sh`（默认恢复模式）。

数据库迁移不随应用镜像回滚自动回退，回滚前必须确认 Schema 向后兼容。

## 11. 部署后健康检查

必须验证：

- PostgreSQL healthy。
- Redis healthy。
- API container healthy。
- Knowledge Worker healthy。
- Chat Import Worker healthy。
- Console container healthy。
- 内部 API readiness：`/health/ready`。
- 内部 Console health：`/healthz`。
- 公网 API：`https://api.wet001.com/health/ready`。
- 公网 Console：`https://bot.wet001.com/`，页面包含 SuperNova 标识。
- 服务器部署 SHA 与本次目标 SHA 完全一致。

任何一项未通过，部署不得标记为成功，部署队列必须保持阻断。

## 12. 中央项目管理会话职责

中央项目管理会话负责：

- 分配任务与会话 A/B/C 职责。
- 检查分支是否从最新主开发分支创建。
- 协调共享文件、API 和迁移依赖。
- 审核 PR Diff。
- 检查 CI 与安全测试。
- 控制 PR 合并顺序。
- 确认部署 SHA。
- 检查 Staging 健康状态。
- 完成 Staging acceptance。
- 决定是否允许下一轮 PR 合并。

任何开发会话均不拥有独立部署权限。

## 13. 本规范的生效边界

本规范合并到统一主开发分支后，自动化机制正式生效。

在 GitHub Branch protection 和 `staging-acceptance` Environment Required reviewers 配置完成前，PR 合并和人工 Staging 验收仍由中央项目管理会话执行，不得将“代码已提交”视为“平台设置已生效”。
