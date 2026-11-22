# Third-party Chat Completions API v1

## Endpoint

`POST /v1/chat/completions`

This extends the existing OpenAI-compatible SuperNova Gateway. It does not create a second chat endpoint and retains the existing Bearer API Key authentication, Persona model routing, Knowledge Base retrieval, usage metering and response format.

## Request

The existing required fields remain unchanged:

- `model`: enabled SuperNova Persona slug.
- `messages`: OpenAI-compatible message array.

Two optional fields are added:

- `user`: external contact identifier supplied by the caller. For the current WeChat integration this is the contact wxid.
- `sender_profile`: contact display metadata supplied by the caller.

Example:

```json
{
  "model": "customer-support",
  "messages": [
    {
      "role": "user",
      "content": "你好，请介绍一下你们的产品"
    }
  ],
  "user": "wxid_xxx",
  "sender_profile": {
    "wxid": "wxid_xxx",
    "nickname": "张三",
    "remark": "张总",
    "avatar_url": "https://example.com/avatar.png",
    "labels": ["意向客户", "已成交"]
  }
}
```

Validation rules:

- `sender_profile.wxid` is required when `sender_profile` is present.
- If both `user` and `sender_profile.wxid` are supplied, they must match.
- If `sender_profile` is present and `user` is omitted, SuperNova derives `user` from `sender_profile.wxid`.
- `nickname` and `remark` are limited to 160 characters.
- At most 50 labels are accepted; each label is limited to 80 characters.
- Unknown fields are rejected.

## Processing and privacy

- `nickname`, `remark` and `labels` are appended to the Persona system context as untrusted contact metadata. They are not treated as instructions.
- `wxid` and `avatar_url` are accepted and validated as metadata but are not forwarded to the model provider.
- The external `user` value participates in the idempotency request fingerprint, but is not stored as a contact record and is not forwarded to the model provider.
- No avatar is fetched.
- This initial API increment does not persist sender profiles or build cross-request user histories.

## Account isolation boundary

A contact wxid is not a logged-in WeChat account identifier. Never use `user` or `sender_profile.wxid` to infer the source account.

The established identity boundary remains:

`tenant_id + platform + source_account_id + external_user_id`

Each WeChat login must be bound to a SuperNova-generated `source_account_id` through an account-specific connection credential or a separately validated source-account binding. The current payload alone does not contain that account binding, so this increment deliberately keeps sender metadata request-scoped and does not persist or merge contacts.

Persistent contact profiles and conversation-derived user profiles must be implemented only after the third-party caller supplies or is bound to the correct source account identity.

## cURL example

```bash
curl -X POST "https://YOUR_SUPERNOVA_HOST/v1/chat/completions" \
  -H "Authorization: Bearer sn_live_REPLACE_WITH_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: wechat-message-unique-id" \
  -d '{
    "model": "customer-support",
    "messages": [
      {"role": "user", "content": "你好，请介绍一下你们的产品"}
    ],
    "user": "wxid_xxx",
    "sender_profile": {
      "wxid": "wxid_xxx",
      "nickname": "张三",
      "remark": "张总",
      "avatar_url": "https://example.com/avatar.png",
      "labels": ["意向客户", "已成交"]
    }
  }'
```

A successful call returns the standard OpenAI-compatible `chat.completion` response, including SuperNova's `supernova.citations` metadata.

## Error behavior

- `401`: missing, invalid, revoked or expired API Key.
- `404`: Persona model does not exist or is not available to the tenant.
- `422`: invalid request fields or mismatched `user` and `sender_profile.wxid`.
- `502` / `503`: provider or Knowledge Base retrieval failure.

## Acceptance

1. The example payload passes request validation.
2. A successful non-streaming request reaches the configured Persona provider and returns a normal completion.
3. A streaming request continues to use the existing SSE behavior.
4. Nickname, remark and labels appear in the model context.
5. wxid and avatar URL are not forwarded to the provider.
6. A mismatched `user` and `sender_profile.wxid` returns 422.
7. Existing requests without the two optional fields remain backward compatible.
8. Existing usage metering, RAG retrieval, citations and idempotency behavior remain intact.
9. No contact profile or chat transcript is persisted by this increment.
