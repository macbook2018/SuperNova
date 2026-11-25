# 香港 RAG 候选环境操作与验收

总控使用本环境对未合并的 RAG PR 做香港实测。部署发起者须有维护权限；可由独立协作者复核，也可由项目总控按本手册确认具体提交。保留部署与业务验收两项记录。

## 启用入口

1. 将候选部署治理 PR 单独审入受保护的默认分支 `main`。核对工作流、校验器、Compose 和主机脚本全部来自同一受信提交。候选功能 PR 保持独立，日常功能分支推送及 CI 继续沿用现有流程。
2. 在仓库创建 Environment `hk-rag-candidate`，部署分支只允许 `main`。有两位可用维护者时，可设置 Required reviewers、Prevent self-review 和禁止管理员跳过，并指定独立发布审核人。若仓管选择单维护者审核，由有 write/maintain/admin 权限的总控按下节输入完整确认文本。已启用的 GitHub 环境审核照常生效；同一 GitHub 账号的多个会话只算一个账号。GitHub 对环境审核名单只要求其中一人批准。[GitHub 环境审核说明](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments#required-reviewers)
3. 默认 `ssh_profile=candidate`。在候选 Environment 配置专属的 `HK_CANDIDATE_SSH_HOST`、`HK_CANDIDATE_SSH_USER`、`HK_CANDIDATE_SSH_KEY`、`HK_CANDIDATE_SSH_KNOWN_HOSTS`，非 22 端口再设 `HK_CANDIDATE_SSH_PORT`。这些名称仅在候选 Environment 配置，经独立渠道核对主机指纹。
4. 候选与日常 Staging 共用香港主机时，可显式选 `ssh_profile=existing-staging`。它使用日常部署已配置的仓库 Secrets `STAGING_HOST`、`STAGING_USER`、`STAGING_SSH_PRIVATE_KEY`、`STAGING_SSH_KNOWN_HOSTS` 与可选的 `STAGING_SSH_PORT`。在候选 Environment 中保持这些名称为空缺，以免遮蔽仓库配置。仅复用 SSH 连接，候选仍有独立目录、Compose 项目和数据。部署与日常 Staging 共用锁；部署身份需要有权访问该锁。
5. 在 Linux amd64 候选机上，部署账号须能创建或访问权限 0700 的固定目录 `/opt/supernova-hk-candidate`。控制器要求可见至少两个 CPU、1600 MiB 可用物理内存，并按归档和上轮候选卷大小检查本机空间。首次部署自动初始化该目录并生成独立的数据库、Redis、Silo、JWT 和加密密钥。可提前用受信 `main` 中的 `deploy/hk-candidate/hk-rag-candidate-host.sh init` 初始化。BYOK 验收可保留初始 `secrets.env`（权限 0600）中的 `HK_ADMIN_EMAILS` 为空和 `HK_MODEL_CREDENTIALS_JSON='{}'`。验收平台池时在 `HK_MODEL_CREDENTIALS_JSON` 添加候选专属 credential ref，并在供应商侧设置用量限额。预留管理员邮箱只用于已有账号的迁移引导，空库填写它不会创建拥有管理权限的账号。就绪后在候选 Environment 启用 `HK_RAG_CANDIDATE_ENABLED=true`。

## 发起一次候选部署

先查看候选 PR 的完整 40 位 head SHA、当前 base、CI 和审核。使用独立审核时，由 PR 作者以外、有 write/maintain/admin 权限的协作者在该 SHA 提交有效的 APPROVED review。总控也可在核对完整变更、CI 和上游提交后对 Draft 或待审 PR 显式确认。A1 对应 `feat/rag-65a-versioned-index`；A2 对应 `feat/rag-65a-structured-chunking`，还须核对其包含本轮 A1 的已审提交，两层 PR 的审核和 CI 都应满足入口校验。记录 PR URL、目标 SHA 和 CI run URL。候选头更新后重新核对。

在 Actions → **Hong Kong RAG candidate** → **Run workflow** 选择 `main`，填入 `pr_number` 和 `candidate_sha`，根据香港主机选择 `ssh_profile`。也可以从有该仓库权限的终端发起同样的请求。

```bash
gh workflow run hk-rag-candidate.yml -R macbook2018/SuperNova --ref main \
  -f pr_number="$RAG_PR_NUMBER" -f candidate_sha="$RAG_HEAD_SHA"
```

复用香港 Staging 连接时，在上面命令追加 `-f ssh_profile=existing-staging`。

独立审核路径将可选输入 `review_confirmation` 留空。单维护者路径由总控本人在 Actions 中填写 `REVIEWED <candidate_sha>`；A2 填写 `REVIEWED <candidate_sha> WITH A1 <current_a1_sha>`，替换为本轮实际的完整小写 SHA。CLI 可追加 `-f review_confirmation="$RAG_REVIEW_CONFIRMATION"`。运行记录保留确认账号、源提交和输入，审核以当次 run 为准。有环境审核规则时，审核人再次核对本次 run 的 PR、SHA、CI 与环境后批准。运行采用本次构建的 API/Console 镜像、同次运行的归档 ID、镜像 ID 和 SHA-256 校验值。记录最终 run URL、镜像 ID、迁移版本及健康状态。镜像归档只短期保留，业务验收证据单独记在交付记录中。无需发布共享镜像标签。

## 业务验收

通过 SSH 隧道在验收人员本机访问 API 和 Console。将 `HK_SSH_ALIAS` 设为已有 SSH 配置中的候选机别名，在浏览器打开 `http://127.0.0.1:18183`。

```bash
ssh -N -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:18182:127.0.0.1:18182 \
  -L 127.0.0.1:18183:127.0.0.1:18183 "$HK_SSH_ALIAS"
```

| 验收项 | 操作与通过证据 |
| --- | --- |
| 候选资源 | 核对 API `http://127.0.0.1:18182/health/ready`、Console、知识 worker、候选 PostgreSQL、Redis 和 Silo。核对容器所属项目、卷、端口和迁移 head 与本次 release 一致。 |
| 真实模型 | 普通测试租户在「模型供应商」添加专用的 OpenAI 兼容 Embedding 供应商和对话供应商。在知识库的「Embedding 来源」选「使用自己的供应商 API Key（BYOK）」，填写供应商实际 Qwen3-Embedding-8B 模型 ID，维数为 1536。Persona 的「模型来源」也选 BYOK，绑定对话模型与本库。导入样本并检索，以 worker 成功索引、正确答案与引用证明确实跑通真实模型。 |
| 索引发布与回滚 | 先用含已知事实的 TXT/Markdown 建立可检索的旧索引。构建候选后、发布前用同一检索确认仍读旧版。发布后验证命中、片段及引用，再回退到旧索引并复查。A2 另选 `structured_v1`，测试标题与正文、短段合并以及 PDF 页码或表格来源。 |
| API 与隔离 | 使用普通搜索和 `/v1/chat/completions` 的流式、非流式请求询问同一事实，保存预期答案与实际引用。以第二个测试租户查询第一个租户的库、文档与版本，验证访问拒绝、无资料泄露。对查无依据的问题记录检索与回答。 |
| 成本与结论 | 核对实际 Embedding 调用、tokens、失败或未知用量标记。记录样本、原版/候选结果、失败项及总控结论。报告只保存脱敏片段、用量和请求 ID。业务验收通过后由总控决定功能 PR 的下一步。 |

## 中止与恢复

构建、校验或环境审核失败时，查看失败环节，修复后以当前获审 SHA 发起新运行。主机部署失败时先核对脚本报告和两个本地健康端点；已有成功候选时检查前一发布是否自动恢复，首次失败记录为无可恢复发布。当前候选项目记录在 `/opt/supernova-hk-candidate/current`，对应控制器与环境快照位于 `releases/<project>/`。需要手动回退时，在候选机使用来自已审核 `main` 的 `hk-rag-candidate-host.sh rollback`，再核对恢复的 SHA、健康状态和原有样本。该操作针对候选项目及其数据快照。

验收结束后关闭 SSH 隧道，用同一可信主机脚本的 `stop` 停止当前候选项目并保留卷。模型凭据通过其提供方轮换或撤销；留存旧发布配置时应考虑其中包含候选凭据。磁盘空间不足时暂停发布，由总控确认候选快照保留范围和备份后再清理。


<details>
<summary>另验平台模型池时，在新候选库给专人临时配置权限</summary>

若已有候选管理员，请用现有账号管理模型。全新候选库的 RBAC 迁移仅给当时已存在的预留账号授权，公共注册也会拒绝保留邮箱。在 Console 用专属的**非保留邮箱**注册，使用普通账号的 `GET /auth/me` 核对 `user.id` 和邮箱。在候选机由总控执行下列一次性命令，设置 `HK_EVAL_USER` 与 `HK_EVAL_EMAIL` 为刚核对的值。该授权仅含模型读取和配置，四小时后过期；同一候选 release 重复执行不会延长或重复授权。

```bash
set -euo pipefail
hk_project=$(cat /opt/supernova-hk-candidate/current)
[[ "$hk_project" =~ ^sn-hkc-[0-9a-f]{12}-[0-9a-f]{12}$ ]]
hk_pg=$(docker ps -q --filter "label=com.docker.compose.project=$hk_project" \
  --filter label=com.docker.compose.service=postgres)
[[ "$hk_pg" =~ ^[0-9a-f]{12,64}$ ]]
docker exec -i "$hk_pg" psql -X -v ON_ERROR_STOP=1 -U hk_candidate -d hk_candidate \
  -v project="$hk_project" -v uid="${HK_EVAL_USER:?}" -v email="${HK_EVAL_EMAIL:?}" <<'SQL'
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('hk_candidate_model_eval'));
SELECT set_config('hkeval.uid', :'uid', true), set_config('hkeval.email', :'email', true),
       set_config('hkeval.project', :'project', true);
DO $$
DECLARE
  target uuid;
  role_key uuid;
  grant_key uuid;
  perms text[];
BEGIN
  IF current_database() <> 'hk_candidate' OR current_user <> 'hk_candidate' THEN
    RAISE EXCEPTION 'Candidate database required';
  END IF;
  SELECT id INTO STRICT target FROM users WHERE id = current_setting('hkeval.uid')::uuid
    AND lower(email) = lower(current_setting('hkeval.email')) AND status = 'active';
  INSERT INTO platform_roles (id, code, name, description, is_system)
    VALUES (gen_random_uuid(), 'hk_eval_models', '香港候选模型验收', '限时模型管理', false)
    ON CONFLICT (code) DO NOTHING;
  SELECT id INTO STRICT role_key FROM platform_roles WHERE code = 'hk_eval_models' AND NOT is_system;
  INSERT INTO platform_role_permissions (role_id, permission_id)
    SELECT role_key, id FROM platform_permissions WHERE code IN ('platform.models.read','platform.models.manage')
    ON CONFLICT DO NOTHING;
  SELECT array_agg(p.code::text ORDER BY p.code) INTO perms FROM platform_role_permissions rp
    JOIN platform_permissions p ON p.id = rp.permission_id WHERE rp.role_id = role_key;
  IF perms IS DISTINCT FROM ARRAY['platform.models.manage','platform.models.read'] THEN
    RAISE EXCEPTION 'Unexpected permission set';
  END IF;
  grant_key := md5(current_setting('hkeval.project') || ':hk_eval_models:' || target::text)::uuid;
  INSERT INTO platform_user_roles (id, user_id, role_id, expires_at)
    VALUES (grant_key,target,role_key,now()+interval '4 hours') ON CONFLICT (id) DO NOTHING;
  IF FOUND THEN
    INSERT INTO platform_admin_audit_logs
      (id,target_user_id,action,resource_type,resource_id,result,reason,details)
      VALUES (gen_random_uuid(),target,'platform.role.bootstrap','user',target::text,'success',
              '香港候选机本地操作，模型权限四小时',json_build_object('project',current_setting('hkeval.project'),
              'grant_id',grant_key,'role','hk_eval_models'));
  END IF;
END $$;
COMMIT;
SQL
```

重新登录后在 `GET /admin/platform/access` 核对权限，配置平台 Embedding 的模型 ID 与对应 credential ref，测试连接确认 1536 维后启用；为对话模型设置当前生效价格及独立 credential ref。以普通租户新建使用平台 Embedding 的知识库和使用平台对话模型的 Persona，重复同一套样本、引用与隔离验收。记录使用的模式及模型，BYOK 验收结果与平台池验收结果分别填写。

</details>
