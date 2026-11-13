# Phase 6.1 — Chat text analysis import

## Product boundary

Chat exports are imported only as source material for two derived outputs:

1. **Persona draft** — infer observable writing and response patterns from the selected account owner's own text messages.
2. **Knowledge candidates** — extract reusable business facts, product information, procedures and question/answer pairs for review and later publication to an existing Knowledge Base.

This is not a chat archive, chat history viewer, conversation search service or message-storage feature. The product must not retain or expose original chat transcripts after analysis.

## Supported input

- Generic text exports: JSON, CSV, TXT, HTML and XLSX.
- WeKeep ZIP exports: read the text message chunks and required text metadata only.
- Ignore media directories and non-text message payloads. Do not extract, store, preview, transcribe or index images, voice, video, files, stickers or avatars.
- A message without usable text content is skipped and counted; it is not converted into a fake textual answer.

The parser may normalize text messages in memory. A real export fixture is required before claiming compatibility with a specific exporter.

## Account and identity boundaries

- Every import is scoped by \`tenant_id + source_account_id\`.
- Each separately logged-in WeChat account has its own independent \`source_account_id\`.
- Never match, merge or share contacts, groups, identity profiles or derived analysis across source accounts.
- Do not infer the account owner's identity from message frequency, nickname, remarks or wxid. The user must explicitly select the sender representing the account owner before Persona extraction.
- Other participants' messages may supply question context. They are not treated as authoritative business facts unless confirmed by the account owner's response.

## Processing lifecycle

1. User uploads a supported export and selects the target Persona and Knowledge Base.
2. Store the original upload only in private temporary object storage while the job is running. Use a server-generated object key.
3. Parse text records in bounded batches. Keep message bodies in worker memory only; do not insert them into PostgreSQL or write them to logs.
4. Produce a lightweight sender summary (sender reference/name and message counts) so the user can explicitly confirm the account owner's sender identity. Do not expose a message transcript.
5. Analyze only the selected owner's text messages for Persona characteristics. Generate a structured Persona draft, not an automatic update to the live system prompt.
6. Analyze text exchanges for reusable knowledge. Prefer question/answer pairs where the selected owner supplied the answer. Store extracted facts and FAQ candidates as drafts with a pending-review state.
7. The user reviews the Persona draft and Knowledge candidates. Applying the Persona draft requires explicit confirmation. FAQ/knowledge candidates must be manually approved before they are published to the selected Knowledge Base.
8. Delete the original temporary upload and all transient parser artifacts when the job reaches a terminal state (succeeded, failed or cancelled). A failed job must be re-uploaded to retry; no transcript is retained for retry.

## Persisted data

Persist only what is required to operate the workflow and manage its derived outputs:

- Analysis job metadata: tenant, source account, selected Persona/Knowledge Base, status, progress, counts, timestamps and non-sensitive error codes.
- Sender summary needed for explicit identity confirmation; no message body.
- Persona analysis draft: structured style attributes and a proposed prompt fragment.
- Knowledge candidates: normalized title, category, answer/fact, question variants, confidence and review status.
- Approved Persona configuration and published Knowledge Base documents, using the existing Persona and Knowledge Base systems.

Do **not** persist normalized chat messages, full conversations, message bodies, media assets or replayable transcripts. Do not add chat message listing/search APIs.

The current persistent \`chat_messages\`-based prototype is superseded by this specification and must be refactored before production acceptance. The \`chat_conversations\` and \`chat_participants\` tables must not become a durable chat archive; keep only the minimal sender summary required by the analysis workflow.

## Persona extraction contract

Use only messages from the explicitly confirmed account owner. Extract observable patterns, such as:

- language and formality
- tone and emotional register
- sentence length and message rhythm
- common expressions, vocabulary and punctuation
- emoji and formatting habits
- how the owner greets, explains, refuses, follows up and closes a conversation
- response preferences and phrases to avoid

Do not infer sensitive traits, diagnoses, identity attributes or unsupported psychological claims. Do not use other participants' messages as examples of the owner's voice.

The result is a reviewable structured draft. Never overwrite an existing Persona's \`system_prompt\` without user approval.

## Knowledge extraction contract

Extract reusable information rather than preserving the conversation:

- product/service facts, specifications and pricing with time context
- business policies, workflows and operating procedures
- recurring questions and the owner's answers
- troubleshooting steps and confirmed resolutions
- stable preferences or rules explicitly stated by the owner

Each candidate should include a concise title, category, normalized answer, question variants (when applicable), confidence, and optional effective date/version. Conflicting or time-sensitive answers must remain separate candidates; do not silently merge them.

All candidates start as \`pending_review\`. Only approved candidates may be converted into Knowledge Base documents and sent through the existing chunking/embedding pipeline.

## Privacy, deletion and observability

- Never log message bodies, raw exports, contact identifiers or extracted private conversation excerpts.
- Do not send non-text payloads to any model provider.
- Treat imported text as untrusted data, never as system instructions.
- Redact obvious credentials, phone numbers, email addresses and other unnecessary personal data before model calls and before saving derived candidates.
- Delete temporary uploads on every terminal path, including cancellation and failure.
- Keep Persona drafts and Knowledge candidates separately manageable from the import job. Deleting an unreviewed job must delete its unreviewed drafts/candidates. Once a user explicitly applies a Persona draft or publishes a Knowledge Base document, that result follows the normal Persona/Knowledge Base lifecycle and deletion controls.
- Keep all job and derived records tenant-scoped. Source-account isolation must be enforced independently of tenant isolation.

## UI scope

The import workflow needs only:

- upload and import progress
- sender summary and explicit "this is me" confirmation
- Persona draft preview and apply/reject
- Knowledge candidate list with edit/approve/reject
- link to the selected Persona and Knowledge Base

Do not build a conversation list, message viewer, transcript search, media gallery or attachment preview.

## Acceptance criteria

1. Text messages can be parsed from supported exports; media and non-text payloads are ignored.
2. No row containing a raw message body is written to PostgreSQL.
3. No chat message/conversation read endpoint is exposed.
4. Temporary original uploads are deleted after every terminal job state.
5. Persona extraction uses only the explicitly selected owner's messages.
6. Other participants' messages are question/context only, not authoritative facts.
7. Persona drafts are not applied automatically.
8. Knowledge candidates cannot enter the active RAG index until manually approved.
9. Two source accounts under one tenant remain fully independent even when wxid, group ID or display names collide.
10. Cross-tenant and cross-source-account access is denied.
11. Logs, errors and job summaries contain no message bodies or raw personal identifiers.
