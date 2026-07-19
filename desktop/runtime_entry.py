#!/usr/bin/env python3
"""Minimal entry point for the VortoCode Desktop bundled runtime.

The sidecar intentionally exposes only the local Gateway server command. The
full ``vc`` CLI remains the user-facing entry point for TUI, headless and
maintenance workflows.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence


def local_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1024 and 65535")
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vortocode-runtime",
        description="VortoCode Desktop bundled local Gateway runtime",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    subcommands = parser.add_subparsers(dest="command", required=True)
    server = subcommands.add_parser("server", help="start the local Gateway server")
    server.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    server.add_argument("--port", type=local_port, default=8080)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "server":
        os.environ["VORTOCODE_DESKTOP_SIDECAR"] = "1"
        from src.web.server import start_server

        start_server(args.host, args.port)
        return 0
    raise AssertionError(f"unsupported runtime command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
