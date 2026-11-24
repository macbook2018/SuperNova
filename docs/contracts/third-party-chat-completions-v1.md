# WeXTool / SuperNova API 对接契约 v1

依据：用户提供的《自定义模型 API 对接说明.md》（2026-11-22）。

本文区分两类状态：
- **本 PR 已实现**：SuperNova 网关当前分支的请求接收、校验和转发行为。
- **尚未实现**：源文档要求、但当前网关没有对应能力的服务端功能。

## 1. Chat Completions

### Endpoint

`POST /v1/chat/completions`

请求头：

```http
Authorization: Bearer <SuperNova API Key>
Content-Type: application/json
Accept: application/json
```

WeXTool 当前固定发送 `stream: false`。网关保留原有 OpenAI 兼容流式能力，但该客户端不使用 SSE。

### 1.1 字段定义

| 字段 | 类型 | 含义 | 接收规则 |
|---|---|---|---|
| `model` | string | SuperNova 人格 slug，例如 `dafeiyu` | 必填 |
| `user` | string | 当前会话 ID；私聊通常为联系人 wxid，群聊为群 ID | WeXTool 始终发送；为兼容旧 OpenAI 客户端，网关仅在出现微信身份字段时强制要求 |
| `account_wxid` | string | 当前登录微信账号 wxid | 可选，仅身份核验成功时发送 |
| `messages` | array | 当前消息数组；WeXTool 当前只发送一条 | 必填 |
| `messages[].role` | string | 消息角色 | 当前 WeXTool 固定为 `user` |
| `messages[].content` | string 或 array | 文字、语音转写文字或多模态内容 | 必填 |
| `stream` | boolean | 是否流式返回 | WeXTool 固定为 `false` |
| `sender_profile` | object | 当前消息实际发言人资料 | 资料读取、核验成功时发送 |
| `sender_profile.wxid` | string | 实际发言人 wxid | profile 存在时必填 |
| `sender_profile.nickname` | string | 微信昵称 | 可为空字符串；最多 256 个 UTF-16 单元 |
| `sender_profile.remark` | string | 当前登录微信为此人设置的备注 | 无备注时为 `""`；最多 256 个 UTF-16 单元 |
| `sender_profile.labels` | array<string> | 微信联系人标签名称 | 无标签时为 `[]`；最多 64 项，每项最多 128 个 UTF-16 单元 |
| `speaker` | string | 部分群语音转写请求中的发言人原生标识 | 可选；不保证是昵称 |

网关对昵称、备注、标签去除控制字符；标签按名称去重。未知字段会返回 422。

### 1.2 身份字段语义

三个身份字段不可混用：

- `account_wxid`：当前登录微信账号。
- `user`：当前会话 ID。
- `sender_profile.wxid`：当前消息实际发言人。

群聊示例中，`user` 保持群 ID（包括 `@chatroom`），不同群成员发言时只改变 `sender_profile.wxid` 和可选的 `speaker`。网关不要求 `user == sender_profile.wxid`。

`account_wxid`、`user`、`speaker` 和 `sender_profile.wxid` 参与请求幂等指纹，但不会作为自定义字段转发给模型供应商。昵称、备注和标签会作为不可信的发言人背景加入 Persona 系统上下文。

### 1.3 纯文字私聊示例

```json
{
  "model": "dafeiyu",
  "user": "wxid_sender",
  "account_wxid": "wxid_logged_in",
  "messages": [
    {
      "role": "user",
      "content": "你好"
    }
  ],
  "stream": false,
  "sender_profile": {
    "wxid": "wxid_sender",
    "nickname": "张三",
    "remark": "张总",
    "labels": ["意向客户", "已成交"]
  }
}
```

### 1.4 群语音转写后的模型请求示例

```json
{
  "model": "dafeiyu",
  "user": "123456789@chatroom",
  "account_wxid": "wxid_logged_in",
  "messages": [
    {
      "role": "user",
      "content": "今天下午三点开会"
    }
  ],
  "stream": false,
  "speaker": "wxid_group_member",
  "sender_profile": {
    "wxid": "wxid_group_member",
    "nickname": "李四",
    "remark": "",
    "labels": []
  }
}
```

### 1.5 身份字段缺失

- `account_wxid` 和 `sender_profile` 都是可选字段。
- 不允许把 `sender_profile.wxid` 填入 `account_wxid`。
- 不允许把上一条消息的账号或联系人资料沿用到下一条消息。
- 当请求包含 `account_wxid`、`sender_profile` 或 `speaker` 时，网关要求同时提供 `user`。
- 不提供微信身份字段的既有 OpenAI 兼容调用保持可用。

### 1.6 图片和表情

`messages[].content` 支持字符串，也支持由以下内容段组成的数组：

```json
[
  {
    "type": "text",
    "text": "[旺柴]"
  },
  {
    "type": "image_url",
    "image_url": {
      "url": "data:image/png;base64,<表情图片Base64>"
    }
  }
]
```

图片规则：

- 支持 `image/jpeg`、`image/png`、`image/gif`、`image/webp`。
- 必须使用 Base64 Data URL。
- 每条消息最多一个图片媒体段。
- 单个图片文件最大 20 MiB。
- 不支持上传本机文件路径、文件名或微信下载凭证。
- 不支持 `detail` 等额外图片字段。
- 内置表情可以将文本段和表情图片段一起放入 content 数组。

图片内容会按 OpenAI 兼容格式转发给人格所配置的模型供应商。供应商模型必须支持视觉输入；不支持视觉的模型可能返回上游请求错误。RAG 检索只使用 content 中的文字段，不会把 Base64 图片内容当作文本检索。

### 1.7 模型成功响应

网关返回 OpenAI 兼容的 `chat.completion` JSON，并补充 `supernova.citations`。WeXTool 当前读取：

`choices[0].message.content`

当前请求固定非流式；响应中的 id、model、usage 等可提供。实际回复仍要求存在非空文本。

## 2. 语音转写

### 2.1 WeXTool 到 SuperNova 的公开接口

`POST /v1/audio/transcriptions` 使用与聊天接口相同的 SuperNova API Key：

```http
Authorization: Bearer <SuperNova API Key>
Content-Type: multipart/form-data
```

multipart 字段：

| 字段 | 类型 | 规则 |
|---|---|---|
| `file` | 二进制文件 | 必传，原始 SILK；上传文件名以 `.silk` 结尾，MIME 必须为 `audio/silk` |
| `model` | string | 可选；如提供，必须与平台配置的转写模型 ID 一致 |

转写接口不上传 `user`、`account_wxid`、`sender_profile` 或 `speaker`。这些身份信息只在后续聊天请求中使用。

成功响应：

```json
{
  "text": "今天下午三点开会"
}
```

`text` 必须为非空字符串，最多 131072 个字符。客户端再将该文字作为 `messages[0].content` 调用 `/v1/chat/completions`；群语音请求可在后续聊天请求中附带 `speaker` 和 `sender_profile`。

### 2.2 SuperNova 到平台语音服务的代理配置

该接口由 SuperNova 统一代理。WeXTool 只使用 SuperNova API Key；上游语音服务地址、模型和 API Key 均由平台控制，不向租户客户端暴露，也不使用租户人格绑定的聊天模型 ID。SuperNova 在内存中将原始 SILK V3 解码为单声道、16-bit PCM WAV，再向上游发送 `message.wav`（MIME `audio/wav`）。WeChat 的 `0x02` 腾讯前缀和标准 `#!SILK_V3` 文件头均受支持；不会创建临时音频文件。

平台服务端配置：

- `SUPERNOVA_AUDIO_TRANSCRIPTION_BASE_URL`：上游语音服务 API 根地址。
- `SUPERNOVA_AUDIO_TRANSCRIPTION_MODEL`：平台选定的 SILK 转写模型。
- `SUPERNOVA_AUDIO_TRANSCRIPTION_CREDENTIAL_REF`：平台凭据引用，实际密钥存放在 `SUPERNOVA_PLATFORM_CREDENTIALS_JSON` 对应键下。
- `SUPERNOVA_AUDIO_TRANSCRIPTION_MAX_FILE_BYTES`：原始 SILK 上传大小上限，默认 25 MiB。
- `SUPERNOVA_AUDIO_TRANSCRIPTION_TIMEOUT_SECONDS`：上游超时，默认 120 秒。
- `SUPERNOVA_AUDIO_TRANSCRIPTION_SAMPLE_RATE`：解码 WAV 采样率，默认 24000 Hz；支持 8000、12000、16000、24000、48000 Hz。
- `SUPERNOVA_AUDIO_TRANSCRIPTION_MAX_DECODED_BYTES`：解码 PCM 大小上限，默认 64 MiB，用于限制内存占用。

前三项必须同时配置。上游语音服务必须接受 WAV 文件。SuperNova 会验证上传 MIME 和文件大小、校验 SILK V3 文件头、限制解码后 PCM 大小、固定上游文件名及 MIME，并校验返回的 `text`。原始 SILK 和转换后的 WAV 仅在请求内存中处理，不持久化；不会记录或输出音频内容及上游 API Key。

### 2.3 用量计量

语音转写使用平台语音服务，按一次平台模型请求计入套餐的 `platform_model_requests` 和 `api_requests`；不虚构聊天输入/输出 Token 数。实际转写调用会写入用量记录，类型为 `audio_transcription`。幂等键相同且文件内容一致时，返回已保存的转写结果，不重复调用上游。


## 3. 会话隔离与上下文

微信身份建议映射到 SuperNova 内部身份边界：

- `account_wxid` 应先与该租户已绑定的微信来源账号核验，再映射到系统生成的 `source_account_id`。
- 会话上下文身份应使用 `tenant_id + platform + source_account_id + user`。
- 联系人身份应使用 `tenant_id + platform + source_account_id + sender_profile.wxid`。
- 群聊中的 `user` 是群会话 ID；`sender_profile.wxid` 是群成员身份。
- `speaker` 只是可选发言人标识，不应替代已核验的联系人 wxid。
- 不能只凭请求体中的 `account_wxid` 就创建可信来源账号，也不能跨微信登录匹配、合并联系人。

**实现状态：当前 PR 接收并校验这些字段，并将身份字段纳入幂等指纹；尚未建立来源账号绑定、联系人持久化或跨请求会话记忆。** 当前网关也不会存储完整聊天记录。服务端上下文记忆需要在来源账号绑定完成后单独实现。

## 4. 当前不上传的字段

按照源文档，WeXTool 当前不上传：

- `self_wxid`（沿用 `account_wxid`，不新增别名）
- `avatar_url` 或头像文件
- 单独消息 ID、消息时间、消息类型
- 群名称、群成员完整列表
- `tenant_id`、`session_id`、`user_id`
- 原始微信 XML、媒体下载密钥、数据库解密密钥、本机绝对路径
- 完整历史聊天记录
- `temperature`、`max_tokens`、`top_p` 等生成参数

## 5. 对接验收

- 私聊文字：`user` 是对方会话标识，`account_wxid` 是当前登录微信账号。
- 群聊：不同成员发言时 `user` 保持群 ID，`sender_profile.wxid` 随实际发言人变化。
- 群 ID 含 `@chatroom` 时能够正常接收。
- 缺少 `sender_profile` 时仍能处理消息。
- 缺少 `account_wxid` 时不会沿用上一条消息的账号。
- 微信标签按名称接收，允许空数组，去重并清理控制字符。
- 图片/GIF：接受 content 数组和 Base64 Data URL，单条消息最多一个图片段，文件不超过 20 MiB。
- 多模态消息中的文字段可用于 RAG 检索。
- 不把 wxid、account_wxid、speaker、头像地址作为模型供应商自定义字段发送。
- 语音转写：需在独立的转写代理实施后，验证 SILK multipart 上传和 text 响应。
- 登录账号变化：同一联系人在不同登录微信下必须映射到不同 source_account_id。
