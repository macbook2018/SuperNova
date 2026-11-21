"""Lightweight health checks for long-running application workers."""

from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit


def worker_process_running(
    module: str,
    proc_root: Path = Path("/proc"),
    current_pid: int | None = None,
) -> bool:
    """Return whether a Python process is running the requested module."""
    if current_pid is None:
        current_pid = os.getpid()

    for cmdline_path in proc_root.glob("[0-9]*/cmdline"):
        try:
            pid = int(cmdline_path.parent.name)
            if pid == current_pid:
                continue
            argv = [
                part.decode("utf-8", errors="replace")
                for part in cmdline_path.read_bytes().split(b"\\0")
                if part
            ]
        except (OSError, ValueError):
            continue

        if len(argv) >= 3 and argv[1:3] == ["-m", module]:
            return True

    return False


def database_tcp_reachable() -> bool:
    """Check the configured PostgreSQL host without importing SQLAlchemy."""
    database_url = os.environ.get("SUPERNOVA_DATABASE_URL", "")
    try:
        parsed = urlsplit(database_url)
        host = parsed.hostname
        port = parsed.port or 5432
    except ValueError:
        return False

    if not host:
        return False

    try:
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--process-module", required=True)
    parser.add_argument("--check-database", action="store_true")
    args = parser.parse_args()

    if not worker_process_running(args.process_module):
        print(f"Worker process not found: {args.process_module}", file=sys.stderr)
        return 1

    if args.check_database and not database_tcp_reachable():
        print("PostgreSQL TCP connection failed", file=sys.stderr)
        return 1

    print("Worker health check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
