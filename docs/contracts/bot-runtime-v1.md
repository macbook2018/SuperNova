# Bot Runtime Contract v1

- Contract version: 1.0.0
- Status: Initial checkpoint for Phase 8 integration
- Database migration: 0012_bot_runtime
- Baseline: feat/phase-6-knowledge-base-rag
- AstrBot release pin: v4.29.4
- AstrBot commit: 61335414433db721b3f7ecc93f90deafe0a59d78

This document is the shared control-plane/runtime contract. Phase 8 may build against this contract without depending on Worker Manager implementation details. Changes to field meaning, paths, state names, event envelope or platform configuration require coordination with the central project-management session.

## 1. Isolation and ownership

- One BotInstance maps to exactly one isolated AstrBot process/container.
- Every BotInstance belongs to one tenant and exactly one Persona in that same tenant.
- The creator must have an active membership in the owning tenant.
- Runtime credentials are bound to tenant_id + bot_instance_id + persona_id + config_version.
- A runtime process never chooses or overrides tenant_id or persona_id.
- Runtime containers do not receive a Docker socket, database credentials, provider API keys, or the user's full SuperNova API Key.
- Runtime conversation/session data is local to the BotInstance volume and must follow the retention policy in the runtime deployment contract. Phase 6 chat-import source files and analysis inputs are never copied into AstrBot.

## 2. BotInstance resource

The control-plane resource is represented by the following JSON shape:

```json
{
  "id": "uuid",
  "tenant_id": "uuid",
  "persona_id": "uuid",
  "created_by_user_id": "uuid",
  "name": "Support Bot",
  "runtime_version": "4.29.4",
  "desired_state": "running",
  "actual_state": "running",
  "config_version": 1,
  "runtime_provider": "docker",
  "runtime_resource_id": "runtime-provider-resource-id",
  "last_heartbeat_at": "2026-11-22T04:00:00Z",
  "last_error_code": null,
  "created_at": "2026-11-22T04:00:00Z",
  "updated_at": "2026-11-22T04:00:00Z"
}
```

Fields:
- id: globally unique instance identifier.
- tenant_id: owning tenant; derived from the authenticated user for public APIs.
- persona_id: one Persona owned by the same tenant.
- created_by_user_id: active tenant member who created the instance.
- runtime_version: immutable AstrBot release version for this instance.
- desired_state: running, stopped or deleting.
- actual_state: pending, provisioning, starting, running, stopping, stopped, unhealthy, failed or deleting.
- config_version: monotonically increasing configuration revision.
- runtime_provider: docker initially; kubernetes is reserved for a future provider.
- runtime_resource_id: provider-specific resource identifier; never accepted from public clients.
- last_heartbeat_at and last_error_code: latest health signal and sanitized failure code.

## 3. Lifecycle and command semantics

Supported commands:
- start
- stop
- restart
- delete
- update_config

Command states:
- queued
- leased
- succeeded
- failed
- cancelled

The command queue is durable in PostgreSQL. A worker claims commands with a lease and bounded attempts. Expired leases can be reclaimed. Every command has a tenant-scoped idempotency key. Replaying the same key and same command returns the existing command; reusing the key for another instance or command returns 409.

Lifecycle transitions are asynchronous:
- create: desired_state=stopped, actual_state=pending.
- start: desired_state=running; Worker Manager provisions or starts exactly one resource.
- stop: desired_state=stopped; Worker Manager stops the resource.
- restart: desired_state=running; Worker Manager performs a stop/start sequence on the same instance resource.
- delete: desired_state=deleting; Worker Manager revokes credentials, removes container and instance volumes, then removes the database instance and cascaded runtime records.
- update_config: increments config_version; old runtime credentials become invalid and the manager writes a new isolated config before restarting/reloading the runtime.

A repeated start must not create a second container. Worker recovery must reconcile desired_state with the provider's observed state before retrying a command.

## 4. Public control-plane API

All public endpoints require the normal SuperNova access token. The tenant is derived from the authenticated principal; clients must not supply tenant_id to select a tenant.

| Method | Path | Purpose |
|---|---|---|
| GET | /bot-instances | List current tenant instances |
| POST | /bot-instances | Create an instance bound to a same-tenant Persona |
| GET | /bot-instances/{id} | Read instance |
| PATCH | /bot-instances/{id} | Update name or Persona while stopped |
| DELETE | /bot-instances/{id} | Queue asynchronous deletion |
| POST | /bot-instances/{id}/start | Queue start |
| POST | /bot-instances/{id}/stop | Queue stop |
| POST | /bot-instances/{id}/restart | Queue restart |
| GET | /bot-instances/{id}/status | Read desired/actual state and heartbeat |
| GET | /bot-instances/{id}/logs?cursor={uuid} | Read sanitized runtime log/error events |

Lifecycle endpoints accept the optional Idempotency-Key header. A successful lifecycle request returns 202 and a command reference. Logs are metadata-only; message bodies, prompts, model responses and transcripts are prohibited.

## 5. RuntimeProvider interface

Worker Manager is the only component allowed to invoke a runtime provider. The API service and AstrBot containers never control Docker directly.

Provider interface:

```python
class RuntimeProvider:
    def provision(self, instance, runtime_config): ...
    def start(self, resource_id): ...
    def stop(self, resource_id): ...
    def restart(self, resource_id): ...
    def delete(self, resource_id, *, delete_volumes=True): ...
    def inspect(self, resource_id): ...
    def logs(self, resource_id, *, cursor=None, limit=100): ...
```

The first implementation is DockerRuntimeProvider. Kubernetes may implement the same contract later. Provider calls must be idempotent and scoped to a server-generated resource name derived from the BotInstance UUID.

## 6. Runtime configuration

The manager creates one private config directory and one private data directory per BotInstance. The config is generated from server-side records; it is never accepted as a complete client-supplied document.

```json
{
  "schema_version": 1,
  "tenant_id": "uuid",
  "bot_instance_id": "uuid",
  "persona_id": "uuid",
  "runtime_version": "4.29.4",
  "config_version": 1,
  "gateway": {
    "base_url": "https://api.example.com/internal/runtime/v1",
    "credential_ref": "runtime-token-file-or-secret-reference",
    "model": "persona-bound"
  },
  "platforms": [
    {
      "platform": "wechat",
      "channel_account_id": "uuid",
      "credential_ref": "secret-reference-only",
      "config_version": 1
    }
  ],
  "storage": {
    "data_dir": "/var/lib/astrbot",
    "session_retention_days": 0
  }
}
```

platforms[] is the Phase 8 extension point. Each entry may contain only:
- platform: stable platform identifier.
- channel_account_id: SuperNova channel-account identifier.
- credential_ref: reference to a secret held outside the runtime config.
- config_version: channel-account configuration revision.

Do not place channel passwords, cookies, access tokens, model provider keys, or full credential payloads in runtime config. Do not include Persona system_prompt or model provider secrets; the gateway resolves these server-side.

The runtime data directory must be unique to one BotInstance. The default session retention is disabled until an explicit product retention policy is approved. If session persistence is enabled later, it remains instance-local and is not exported to Phase 6 chat-import storage.

## 7. Runtime status and heartbeat

Status response fields:
- bot_instance_id
- tenant_id
- desired_state
- actual_state
- runtime_version
- config_version
- runtime_resource_id
- last_heartbeat_at
- last_error_code

Heartbeat endpoint:

POST /internal/runtime/v1/heartbeat

Example:

```json
{
  "config_version": 1,
  "runtime_version": "4.29.4",
  "state": "running",
  "health_code": null
}
```

The server validates the authenticated credential, config_version and pinned runtime_version. The server time is authoritative. A heartbeat cannot change tenant, bot or Persona identity. Worker Manager separately monitors heartbeat expiry and provider health; missed heartbeats move the instance to unhealthy and enter bounded recovery.

## 8. Internal authentication

Internal endpoints use short-lived signed runtime JWTs. The signing key is independent from user API keys and must be supplied through SUPERNOVA_RUNTIME_JWT_SECRET.

Required claims:
- iss: supernova-runtime
- aud: supernova-runtime
- sub: bot_instance_id
- tenant_id
- persona_id
- config_version
- scope
- iat
- exp: no more than 300 seconds after issuance
- jti: unique, persisted, auditable and revocable

Allowed scopes:
- chat.completions
- runtime.heartbeat
- runtime.events

The credential row binds jti, token hash, tenant_id, bot_instance_id, persona_id, config_version, scope and expiry. Every request rechecks revocation and instance ownership. A credential for one bot cannot be used for another bot, tenant, Persona, scope or configuration revision.

Internal endpoints:
- POST /internal/runtime/v1/heartbeat — runtime.heartbeat
- POST /internal/runtime/v1/events — runtime.events
- POST /internal/runtime/v1/chat/completions — chat.completions

The chat endpoint ignores the client-selected model and binds the request to the authenticated instance's Persona. It calls the existing SuperNova gateway implementation; it does not call a tenant provider directly.

## 9. Event Envelope

Every runtime event uses this envelope:

```json
{
  "event_id": "uuid",
  "event_type": "runtime.state_changed",
  "schema_version": 1,
  "tenant_id": "uuid",
  "bot_instance_id": "uuid",
  "channel_account_id": null,
  "source_account_id": null,
  "platform": "system",
  "correlation_id": "uuid",
  "occurred_at": "2026-11-22T04:00:00Z",
  "payload": {
    "level": "info",
    "component": "worker",
    "state": "running",
    "error_code": null
  }
}
```

event_id is idempotent. tenant_id and bot_instance_id must match the authenticated runtime identity. channel_account_id and source_account_id are nullable for system events and are populated for channel-scoped events. payload is limited to allowlisted operational metadata. Chat text, message content, prompts, model output and transcripts are never persisted in BotRuntimeEvent.

## 10. Unified gateway behavior

Runtime chat flow:

AstrBot Runtime -> runtime JWT -> SuperNova internal runtime chat endpoint -> existing OpenAI-compatible Gateway -> Persona resolution -> platform model or tenant BYOK provider -> Persona-linked Knowledge Base RAG -> upstream response.

The existing gateway remains authoritative for:
- Persona system_prompt and generation parameters.
- platform model versus BYOK provider resolution.
- knowledge-base associations and RAG retrieval.
- token usage records and Phase 5C shadow metering.
- subscription/quota checks.
- no real wallet debit or Phase 5D settlement.

Runtime calls are attributed to bot_instance_id in usage_records. BYOK model token costs are not charged as SuperNova platform-model usage. No second model router or direct provider credentials are introduced in AstrBot.

## 11. AstrBot pin and adapter boundary

The initial runtime image pins the stable AstrBot v4.29.4 release at commit 61335414433db721b3f7ecc93f90deafe0a59d78. The v4.30.0-beta.1 release is not selected because it is a beta release with conversation/session migration changes. No floating latest tag is permitted.

Channel integration must use AstrBot's public plugin platform-adapter API. The official adapter guide is:
https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/docs/en/dev/plugin-platform-adapter.md

Platform adapters translate platform events into AstrBot message events and send responses through the platform SDK. They must not resolve Persona, select providers, or persist chat-import records. Phase 8 may add platform-specific adapter packages without changing this contract.

## 12. Resource and retention controls

Each runtime container must have explicit CPU, memory, PID and storage limits; read-only root filesystem where compatible; non-root user; dropped Linux capabilities; no privileged mode; no host network; no Docker socket; and only required network egress. Config and data volumes are per instance. Worker Manager owns Docker access and performs deletion/volume cleanup.

AstrBot session persistence is isolated per instance. Until a retention policy is explicitly approved, do not enable long-lived transcript/session archive export. Phase 6 chat-import uploads remain temporary and are never mounted into a runtime container.
