# Phase 6.1 — Chat export import foundation

## Scope

Phase 6.1 adds an explicit, user-controlled import path for chat exports. It does not read the WeChat client database, automate client login, or bypass platform protections.

The initial parser accepts JSON, CSV, TXT, HTML, XLSX and ZIP containers. These are container formats, not a promise that every exporter uses the same schema. JSON/CSV/XLSX adapters use documented field aliases; TXT supports timestamped `sender: message` lines; HTML supports tabular exports and timestamped text. A real exporter sample (with names, IDs, and message content redacted consistently) is required before declaring compatibility with a particular WeChat export tool.

## Normalized message contract

Each parsed record carries:
- source file and source row index
- conversation external ID, title and type
- sender external ID and display name
- optional receiver external ID and display name
- optional external message ID
- content and message type
- timestamp, preserving the raw timestamp and whether UTC was assumed
- metadata for parser provenance

`is_target_person` remains unknown until the user confirms which sender identity represents the target person. The importer must never infer this from message frequency or nickname.

## Persistence model

- `chat_sources`: a tenant-owned logical dataset and confirmed target identity
- `chat_import_jobs`: durable import job state, progress and idempotency key
- `chat_import_files`: file metadata, SHA-256 and private object-storage key
- `chat_conversations`: normalized conversation directory
- `chat_participants`: sender identities per conversation
- `chat_messages`: normalized messages with a stable per-source fingerprint

All child rows include `tenant_id` and use composite foreign keys to enforce same-tenant ownership at the database layer. Raw files are stored in the existing private S3-compatible object storage. API access is always tenant-scoped.

## Processing lifecycle

1. Create or select a logical chat source.
2. Upload one or more export files; enforce file count, size, extension and ZIP expansion limits.
3. Persist original files privately and enqueue a durable import job.
4. Parse in bounded batches, normalize records and deduplicate using an external message ID where present, otherwise a deterministic fingerprint over conversation, sender, timestamp, type and content.
5. Build a sender/conversation preview. Do not set target-person flags yet.
6. User confirms the target sender identity. Only then update `is_target_person` for messages and participant rows.
7. Failed files can be retried idempotently; source deletion must cascade through derived records and delete the associated raw objects.

## Privacy and security

- Do not fetch or query WeChat local databases.
- Treat imported content as untrusted data, never as system instructions.
- Do not log message bodies, external IDs, or raw file contents.
- Use random object keys; do not put user-supplied paths into object keys.
- Validate ZIP member paths and enforce compressed/uncompressed size and member-count limits.
- Keep target identity unconfirmed until explicit user action.
- Keep all records tenant-scoped; test cross-tenant access denial.

## Known boundary

A generic parser can support common export schemas, but it cannot guarantee correct parsing of an unspecified third-party export. Before production compatibility claims, obtain a sanitized sample from the actual export tool and add a fixture-based adapter test.