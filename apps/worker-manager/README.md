# SuperNova Worker Manager

This service is the sole Docker control-plane component.

- Runs the durable PostgreSQL runtime-command consumer.
- Owns the Docker SDK connection and is the only service allowed to mount the Docker socket.
- Provisions one Docker network, config directory and data volume per BotInstance.
- Enforces container memory, CPU, PID, read-only-root, dropped-capability and no-new-privileges defaults.
- Never receives model-provider API keys. Runtime credentials are short-lived and bound to one BotInstance and Persona.
- Never stores chat bodies in runtime events.

The service executes lifecycle commands through the API runtime package. The pinned AstrBot runtime image must be built and published before starting real bot instances.

The Docker socket is equivalent to host-root access. Do not mount it into the API, Console, AstrBot containers or any general-purpose worker. Deploy this service on a dedicated host or node with restricted operator access.
