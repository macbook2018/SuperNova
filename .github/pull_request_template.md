## 任务归属

- 会话：A / B / C / Governance
- 任务名称：
- 负责人：
- 关联任务/Issue：

## 分支检查

- [ ] 本分支从最新 `feat/phase-6-knowledge-base-rag` HEAD 创建或已同步
- [ ] PR Base 为 `feat/phase-6-knowledge-base-rag`
- [ ] 本任务使用独立分支
- [ ] 未直接提交主开发分支或 main
- [ ] 未直接部署 Staging

## 变更摘要

说明本 PR 做了什么、为什么做，以及明确不包含什么。

## 文件与接口影响

- 主要修改文件：
- 是否修改共享文件：
- 是否修改 API / Schema：
- 是否新增或修改数据库迁移：
- 迁移依赖：
- 是否影响其他会话任务：

如存在共享文件或接口重叠，必须先协调并记录结果。

## 验证结果

- [ ] Console typecheck / production build
- [ ] Ruff
- [ ] pytest
- [ ] Alembic upgrade
- [ ] Docker Compose config / smoke
- [ ] 相关集成测试
- [ ] 租户隔离 / 权限测试
- [ ] 未使用 Mock 冒充真实端到端验收

测试命令与结果：

## 部署影响

- [ ] 不需要 Staging 部署
- [ ] 需要在 PR 合并到统一主开发分支后进入唯一 Staging 部署流程

涉及环境变量、镜像、迁移、Worker 或回滚策略的变化：

## 风险与回滚

- 风险：
- 回滚方式：
- 数据库向后兼容性：

## 审核清单

- [ ] PR Diff 已自查
- [ ] 没有覆盖其他会话的提交
- [ ] 没有密钥、真实用户隐私或临时数据
- [ ] 文档已同步
- [ ] CI 全部通过
- [ ] 审核意见全部解决
