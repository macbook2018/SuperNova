# 人格、模型供应商与知识库目录模型

## 数据模型

### model_providers

| 字段 | 说明 |
| --- | --- |
| id | UUID 主键 |
| tenant_id | 工作空间归属 |
| name | 工作空间内唯一名称 |
| provider_type | openai_compatible / openai / deepseek / qwen / anthropic |
| base_url | HTTP(S) API 根地址 |
| api_key_ciphertext | Fernet 密文，可为空（允许后续清除） |
| api_key_last4 | 密钥末四位提示 |
| enabled | 是否启用 |
| created_at / updated_at | 创建和更新时间 |

同一工作空间内供应商名称唯一。外部 HTTP 地址要求 HTTPS；仅 localhost、127.0.0.1 和 ::1 允许 HTTP。当前阶段不会主动访问供应商 URL，连通性测试留给后续模型网关阶段，并需增加 SSRF 防护。

### personas

| 字段 | 说明 |
| --- | --- |
| id / tenant_id | 资源 ID 与工作空间归属 |
| name / slug | 展示名称与外部 model 调用标识 |
| description | 人格简介 |
| system_prompt | 系统提示词 |
| provider_id | 同一工作空间的模型供应商，可空 |
| model_name | 供应商内的模型名称 |
| temperature | 0 到 2 |
| top_p | 0 到 1 |
| max_tokens | 1 到 65536 |
| enabled | 人格是否启用 |
| created_at / updated_at | 创建和更新时间 |

slug 只允许小写字母、数字和连字符，并在 tenant 内唯一。

### knowledge_bases

当前阶段仅保存知识库元数据（name、description、tenant_id、created_at、updated_at）。文档、切片、Embedding 与索引任务尚未实现，因此 documents_count 当前为 0，不应向用户展示演示文件。

### persona_knowledge_bases

以 (tenant_id, persona_id, knowledge_base_id) 为复合主键，允许一个人格关联多个知识库，也允许一个知识库被多个同租户人格复用。

Persona 对 Provider 的复合外键、PersonaKnowledgeBase 对 Persona/KnowledgeBase 的复合外键，在数据库层阻止跨 tenant 引用。

## API 约定

所有目录接口均使用 Bearer JWT，并从 CurrentPrincipal 取得 tenant 上下文。

- GET/POST/PATCH/DELETE /model-providers
- GET/POST/GET-by-id/PATCH/DELETE /personas
- GET/POST/PATCH/DELETE /knowledge-bases

删除正在被人格使用的供应商返回 HTTP 409。跨租户资源查询统一返回 404，避免泄露资源是否存在。

人格创建和更新支持 knowledge_base_ids；更新时提供空数组代表清空关联，不提供该字段代表保持原关联。

## 供应商凭据加密

使用 Fernet（AES-128-CBC + HMAC-SHA256）对供应商 API Key 进行可逆加密，供后续模型网关内部解密使用。数据库不存储明文。

- 创建/更新接口接受 api_key。
- 读取接口只返回 has_api_key 和 api_key_hint。
- 日志和错误响应不得包含 API Key 或密文。
- SUPERNOVA_ENCRYPTION_KEY 必须是有效 Fernet URL-safe Base64 key。
- 开发环境默认值仅供本机；生产环境必须设置独立随机密钥。
- 加密密钥轮换需要在切换前重加密全部记录；当前未实现在线多版本密钥轮换。

生成本机 Fernet key：

~~~bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
~~~

将输出保存到 .env 的 SUPERNOVA_ENCRYPTION_KEY。不要将真实 key 提交到仓库。

## 前端行为

- 未登录时展示注册/登录入口。
- 登录后从 /auth/me 恢复身份，API 请求携带 Bearer Access Token。
- Access Token 被拒绝时，客户端尝试一次 Refresh Token 轮换并重试原请求。
- 人格、供应商、知识库元数据和 API Key 列表从后端加载；保存、启停、删除均调用对应接口。
- API Key 创建或轮换后，明文只在一次性弹窗展示。
- 文件上传、真实模型调用、用量图表和账单尚未接入，不显示伪造数据。
