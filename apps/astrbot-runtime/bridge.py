"""Per-instance loopback OpenAI-compatible bridge to the authenticated SuperNova Gateway."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

LOG = logging.getLogger("supernova.runtime")
CONFIG_PATH = Path(os.environ.get("SUPERNOVA_RUNTIME_CONFIG", "/etc/supernova/runtime.json"))
LOOPBACK_PORT = 18789
MAX_REQUEST_BYTES = 512 * 1024


def runtime_config() -> dict[str, Any]:
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not data.get("bot_instance_id"):
        raise RuntimeError("runtime_config_invalid")
    gateway = data.get("gateway")
    if not isinstance(gateway, dict) or not gateway.get("credential_ref"):
        raise RuntimeError("runtime_gateway_config_invalid")
    return data


def runtime_token(config: dict[str, Any], scope: str) -> str:
    # Read on every request; Worker Manager updates this file with atomic rename.
    location = Path(config["gateway"]["credential_ref"])
    credentials = json.loads(location.read_text(encoding="utf-8"))
    token = credentials.get(scope)
    if not isinstance(token, str) or not token:
        raise RuntimeError("runtime_credential_missing")
    return token


def normalized_chat(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Never let a plugin select a different Persona or inject a system prompt."""
    messages = payload.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 1000:
        raise ValueError("invalid_messages")
    safe_messages = []
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("invalid_message")
        role = message.get("role")
        if role in ("system", "developer"):
            continue  # SuperNova Persona is authoritative.
        if role not in ("user", "assistant"):
            raise ValueError("unsupported_message_role")
        content = message.get("content")
        if not isinstance(content, str):
            raise ValueError("text_only_runtime_required")
        safe_messages.append({"role": role, "content": content})
    if not safe_messages or not any(m["role"] == "user" for m in safe_messages):
        raise ValueError("missing_user_message")
    if payload.get("stream", False):
        raise ValueError("streaming_not_supported")
    supported = {
        "temperature", "top_p", "max_tokens", "stop",
        "presence_penalty", "frequency_penalty",
    }
    result = {key: value for key, value in payload.items() if key in supported}
    result.update({
        "model": config["gateway"]["model"],
        "messages": safe_messages,
        "stream": False,
    })
    return result


async def _post_gateway(
    session: aiohttp.ClientSession,
    config: dict[str, Any],
    endpoint: str,
    scope: str,
    body: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    url = config["gateway"]["base_url"].rstrip("/") + endpoint
    headers = {"Authorization": "Bearer " + runtime_token(config, scope)}
    async with session.post(url, json=body, headers=headers) as response:
        try:
            data = await response.json()
        except (aiohttp.ContentTypeError, ValueError):
            return 502, {"error": {"code": "runtime_gateway_invalid_response"}}
        return response.status, data


async def chat_completion(request: web.Request) -> web.Response:
    try:
        config = runtime_config()
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("request_must_be_object")
        body = normalized_chat(payload, config)
        status, data = await _post_gateway(
            request.app["session"], config, "/chat/completions",
            "chat.completions", body,
        )
        return web.json_response(data, status=status)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        return web.json_response(
            {"error": {"message": str(exc), "type": "invalid_request_error"}},
            status=400,
        )
    except (aiohttp.ClientError, TimeoutError, OSError, RuntimeError):
        LOG.warning("gateway_request_failed")
        return web.json_response(
            {"error": {"message": "SuperNova Gateway unavailable", "type": "server_error"}},
            status=503,
        )


async def models(request: web.Request) -> web.Response:
    # Do not forward list-models to an unscoped user-key endpoint.
    config = runtime_config()
    return web.json_response({
        "object": "list",
        "data": [{
            "id": config["gateway"]["model"],
            "object": "model",
            "created": 0,
            "owned_by": "supernova",
        }],
    })


async def ready(_request: web.Request) -> web.Response:
    return web.json_response({"ready": True})


async def start_bridge() -> tuple[web.AppRunner, aiohttp.ClientSession]:
    timeout = aiohttp.ClientTimeout(total=120, connect=10)
    session = aiohttp.ClientSession(timeout=timeout, trust_env=False)
    app = web.Application(client_max_size=MAX_REQUEST_BYTES)
    app["session"] = session
    app.router.add_post("/v1/chat/completions", chat_completion)
    app.router.add_get("/v1/models", models)
    app.router.add_get("/healthz", ready)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", LOOPBACK_PORT).start()
    return runner, session


def storage_root(config: dict[str, Any]) -> Path:
    # Default policy: no conversation/session data survives a container restart.
    # A durable retention period requires explicit product approval and a purge worker.
    if config.get("storage", {}).get("session_retention_days", 0) != 0:
        raise RuntimeError("persistent_sessions_not_approved")
    return Path("/tmp/supernova-astrbot-session")


def bootstrap_astrbot(config: dict[str, Any], root: Path) -> None:
    # The installed, pinned AstrBot package supplies its public default config.
    from copy import deepcopy

    from astrbot.core.config.default import DEFAULT_CONFIG

    data_root = root / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    astrbot = deepcopy(DEFAULT_CONFIG)
    old_path = data_root / "cmd_config.json"
    if old_path.exists():
        existing = json.loads(old_path.read_text(encoding="utf-8"))
        # Preserve channel settings once Phase 8 has installed approved adapters.
        astrbot["platform"] = existing.get("platform", [])
    astrbot["provider_sources"] = [{
        "id": "supernova-gateway",
        "type": "openai_chat_completion",
        "provider": "openai",
        "provider_type": "chat_completion",
        "enable": True,
        "api_base": f"http://127.0.0.1:{LOOPBACK_PORT}/v1",
        "key": ["local-bridge-only"],
        "timeout": 120,
        "proxy": "",
    }]
    astrbot["provider"] = [{
        "id": "supernova-persona",
        "provider_source_id": "supernova-gateway",
        "type": "openai_chat_completion",
        "model": config["gateway"]["model"],
        "enable": True,
    }]
    astrbot["agent_runner"]["config"]["model"]["provider_id"] = "supernova-persona"
    astrbot["dashboard"]["enable"] = False
    astrbot["plugin_set"] = []
    astrbot["disable_builtin_commands"] = True
    astrbot["trace_enable"] = False
    astrbot["log_file_enable"] = False
    astrbot["provider_ltm_settings"]["group_message_history_enable"] = False
    astrbot["provider_ltm_settings"]["active_reply"]["enable"] = False
    astrbot["provider_settings"]["streaming_response"] = False
    astrbot["provider_settings"]["web_search"] = False
    astrbot["provider_settings"]["file_extract"]["enable"] = False
    astrbot["provider_settings"]["computer_use_runtime"] = "none"
    astrbot["platform"] = []  # Channels are disabled pending Phase 8 credential resolution.
    tmp = old_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(astrbot, ensure_ascii=False), encoding="utf-8")
    tmp.replace(old_path)


async def heartbeat_loop(
    session: aiohttp.ClientSession,
    process: asyncio.subprocess.Process,
) -> None:
    while process.returncode is None:
        try:
            config = runtime_config()
            await _post_gateway(
                session, config, "/heartbeat", "runtime.heartbeat", {
                    "config_version": config["config_version"],
                    "runtime_version": config["runtime_version"],
                    "state": "running",
                },
            )
        except (OSError, RuntimeError, aiohttp.ClientError, TimeoutError):
            LOG.warning("runtime_heartbeat_failed")
        await asyncio.sleep(20)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    config = runtime_config()
    root = storage_root(config)
    root.mkdir(parents=True, exist_ok=True)
    # Importing astrbot.core initializes SQLite and configuration immediately.
    # Set the ephemeral root BEFORE any AstrBot import, not just for the child.
    os.environ["ASTRBOT_ROOT"] = str(root)
    bootstrap_astrbot(config, root)
    runner, session = await start_bridge()
    process = await asyncio.create_subprocess_exec(
        sys.executable, "/opt/astrbot/main.py",
        cwd="/opt/astrbot",
        env={**os.environ, "ASTRBOT_ROOT": str(root)},
    )
    task = asyncio.create_task(heartbeat_loop(session, process))
    try:
        exit_code = await process.wait()
        if exit_code:
            LOG.error("astrbot_child_exit exit_code=%d", exit_code)
    finally:
        task.cancel()
        await runner.cleanup()
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
