# AstrBot Runtime — Phase 7

Runtime source pin: AstrBot **v4.29.4**, commit `61335414433db721b3f7ecc93f90deafe0a59d78`.

The per-instance process runs AstrBot and a loopback-only OpenAI-compatible bridge in the same container. AstrBot uses the public OpenAI-compatible provider configuration, never a model-provider API key. The bridge sends every text chat request to `/internal/runtime/v1/chat/completions`, using the runtime-scoped JWT from the per-instance credential file. The file is re-read on every request; JWTs are rotated by Worker Manager.

The bridge strips untrusted runtime-supplied system/developer roles and model selection, to keep the server-side Persona authoritative. Chat streaming and tools are deliberately disabled until the gateway supports their exact request contract. The provider/model configuration is bootstrapped from the pinned AstrBot default configuration; dashboard, built-in commands, local tools, file extraction, traces and network tools are disabled.

Every instance has its own private persistent data mount, but with `session_retention_days=0` (the only currently accepted retention setting), AstrBot's `ASTRBOT_ROOT` points to `/tmp/supernova-astrbot-session` on a container tmpfs instead. AstrBot's SQLite conversation data is therefore ephemeral and is not archived on the instance volume. Nonzero retention fails closed until an explicit product policy and validated deletion worker exist. **This still needs a real-container restart test before Staging conversations are enabled.**

Build locally:

```bash
docker build -f apps/astrbot-runtime/Dockerfile -t supernova-astrbot-runtime:4.29.4-6133541 .
```

Do not claim the image is built/published until a successful actual Docker build. The runtime manager's immutable image tag must point to a verified image. Channel adapters are reserved for Phase 8.
