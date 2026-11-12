from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import HTTPException

from app.core.config import get_settings


def validate_public_service_url(url: str) -> None:
    parsed = urlparse(url)
    host = parsed.hostname
    settings = get_settings()
    development = settings.environment.lower() not in {"production", "prod"}
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    local_target = bool(host and host.lower() in local_hosts)
    if (
        not host
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("service_url_invalid")
    if parsed.scheme != "https" and not (
        development and local_target and parsed.scheme == "http"
    ):
        raise ValueError("service_url_requires_https")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("service_url_invalid_port") from None
    if port not in (None, 443) and not (development and local_target):
        raise ValueError("service_url_port_not_allowed")
    if local_target:
        if not development:
            raise ValueError("service_url_local_target_blocked")
        return
    try:
        literal_ip = ipaddress.ip_address(host)
        addresses = {literal_ip}
    except ValueError:
        try:
            resolved = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
        except OSError:
            raise ValueError("service_url_dns_failed") from None
        addresses = {ipaddress.ip_address(item[4][0]) for item in resolved}
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("service_url_non_public_target")
