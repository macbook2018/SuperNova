# SuperNova

> Multi-tenant AI Agent SaaS built around AstrBot.

SuperNova lets each tenant create independent AI personas, connect knowledge bases and model providers, and expose those personas through WeChat channels or an OpenAI-compatible API.

## Product principles

- **Tenant isolation first**: tenant data, API credentials, knowledge bases, conversations, usage records, and runtime workers must be isolated.
- **AstrBot as the runtime engine**: extend through adapters and integration boundaries; avoid unnecessary forks or invasive changes to upstream core.
- **One API key, multiple personas**: a tenant-level API key authenticates the caller; the `model` field selects a persona owned by that tenant.
- **OpenAI-compatible integration**: support `GET /v1/models`, `POST /v1/chat/completions`, and streaming responses over SSE.
- **Observable and billable**: record request IDs, token usage, latency, errors, and tenant-level consumption.
- **Brand consistency**: use the approved SuperNova orbital S-mark and deep-space navy / cyan / blue / violet visual system.

## Baseline architecture

| Layer | Baseline |
| --- | --- |
| Web console | Vue 3 + TypeScript |
| Control plane API | Python + FastAPI |
| Primary database | PostgreSQL |
| Vector retrieval | pgvector |
| Cache / queue | Redis |
| Object storage | MinIO |
| Agent runtime | Isolated AstrBot workers |
| Deployment | Docker Compose for development; containerized services for production |

The control plane owns tenants, users, API keys, personas, model-provider configuration, knowledge bases, channel configuration, sessions, usage, and worker lifecycle. AstrBot workers execute agent conversations. A unified model gateway provides tenant-aware authentication, persona resolution, provider routing, streaming, and usage accounting.

## API contract

- `GET /v1/models`: list personas available to the authenticated tenant.
- `POST /v1/chat/completions`: resolve `model` to a tenant-owned persona and execute a conversation.
- `stream=true`: emit OpenAI-compatible Server-Sent Events and terminate with `data: [DONE]`.

Authentication uses a tenant-scoped API key in the standard `Authorization: Bearer <key>` header. A key must never grant access to another tenant's persona or data.

## Planned delivery sequence

1. **Foundation** — repository conventions, local development stack, configuration, migrations, health checks, structured logging, and CI.
2. **Control plane** — tenant/user authentication, tenant context, API-key lifecycle, and authorization boundaries.
3. **Persona and model management** — persona CRUD, model-provider configuration, secrets handling, and model selection.
4. **Unified API gateway** — OpenAI-compatible models/chat-completions endpoints, SSE streaming, request tracing, and usage records.
5. **Knowledge** — document/link ingestion, parsing, chunking, embeddings, retrieval, and tenant-scoped access.
6. **AstrBot runtime orchestration** — isolated worker provisioning, lifecycle, adapter integration, health monitoring, and recovery.
7. **Channels and operations** — WeChat integration, session management, usage limits, billing hooks, admin console, and production hardening.

Each phase must have automated tests and an acceptance report. Do not mark a phase complete based only on compilation.

## Repository layout

```text
apps/
  api/                 FastAPI control plane and gateway
  console/             Vue 3 management console
  worker-manager/      AstrBot worker lifecycle and adapter boundary
packages/
  contracts/           Shared API schemas and generated client contracts
  sdk/                 OpenAI-compatible client examples and helpers
deploy/
  docker/              Local and production container definitions
  scripts/             Development and release scripts
docs/
  architecture/        System design and tenant-isolation model
  decisions/           Architecture decision records
  roadmap/             Milestones and acceptance criteria
```

## Security requirements

- Store only hashed API-key verifiers; show the plaintext key only once at creation.
- Keep model-provider secrets encrypted at rest and never return them from read APIs.
- Derive tenant identity from authenticated credentials, never from an untrusted request field.
- Enforce tenant scoping at service and data-access layers; add cross-tenant negative tests.
- Do not expose AstrBot worker credentials, internal URLs, or raw provider secrets to browsers.
- Keep secrets out of source control, logs, traces, and error messages.

## Development status

The repository is being initialized. The first implementation milestone is the foundation and control-plane skeleton; later phases will build on tested interfaces rather than introducing a monolithic application.

## License

License and distribution terms will be decided before the first public release.
