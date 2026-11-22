# 平台 Embedding 服务配置

SuperNova 的对话模型和 Embedding 模型是两类独立资源。Embedding 模型必须实现 OpenAI 兼容的 `POST /embeddings` 接口；不能将聊天模型 ID 当作 Embedding 模型 ID。

## 服务商配置

在统一 Console 的「平台管理 → 平台模型管理 → Embedding 模型池」中创建模型：

- 供应商类型：优先选择「OpenAI 兼容」
- 上游 Embedding 模型 ID：填写服务商文档中的真实 Embedding 模型 ID
- Base URL：填写服务商提供的兼容 API 根地址，例如 `https://api.provider.example/v1`
- 凭据引用：填写一个稳定的引用名，例如 `siliconflow-embedding-primary`
- 向量维度：当前固定为 1536

系统会将 Base URL 规范化为 `/embeddings` 请求地址。不要填写聊天补全模型 ID，也不要把聊天模型的 Base URL 当作 Embedding 服务地址。

## 凭据管理

真实 API Key 不存储在模型表，也不从管理 API 返回。平台管理员在 Staging/生产服务器的受保护环境配置中维护 `SUPERNOVA_PLATFORM_CREDENTIALS_JSON`：

```json
{
  "deepseek-primary": "<chat-provider-api-key>",
  "siliconflow-embedding-primary": "<embedding-provider-api-key>"
}
```

以上仅为格式示例；请替换为服务商实际签发的密钥。不要将真实密钥提交到 GitHub、写入仓库文件或发送到聊天窗口。

API、Knowledge Worker 和其他调用 Embedding 的后台进程必须使用同一份 `SUPERNOVA_PLATFORM_CREDENTIALS_JSON`。Staging Compose 的 API 与 Knowledge Worker 共用应用环境配置锚点；修改服务器环境变量后，必须重建/重启这两个服务，使新配置生效。

## 连接测试和 1536 维校验

保存模型后，在 Embedding 模型列表点击「测试连接」。测试会：

1. 在服务器解析 `credential_ref`，不会把 API Key 返回给浏览器；
2. 请求服务商的 OpenAI 兼容 Embeddings 接口；
3. 检查响应中是否恰好返回一个向量；
4. 检查向量长度是否严格等于 1536；
5. 记录不含密钥的成功审计事件。

只有返回 1536 维向量的模型才能用于当前知识库索引。当前向量列和知识库配置固定为 1536 维；不能仅修改模型表中的维度数字来兼容其他维度。若未来需要其他维度，应单独设计新向量列/索引和知识库重建迁移，不能与现有索引混用。

## 客户知识库创建流程

1. 平台管理员配置模型及服务器凭据。
2. 保存后执行连接测试，确认 1536 维输出。
3. 启用通过测试的 Embedding 模型。
4. 确保 API 和 Knowledge Worker 已加载相同的凭据环境变量。
5. 客户重新加载 Console；`GET /platform/embedding-models` 仅返回已启用且服务器凭据引用存在的模型。
6. 创建知识库时选择平台 Embedding 模型。知识库不需要对 Embedding 模型配置对话 Token 价格版本。
7. 上传文档后由 Knowledge Worker 使用该知识库绑定的模型生成向量。

如果模型未出现在知识库下拉框，先检查模型是否启用、`credential_ref` 是否与 JSON 键名完全一致，以及 API 是否已重启加载新环境变量。若仍有问题，在平台管理页运行连接测试并查看明确错误。
