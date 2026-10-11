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
    subcommands.add_parser("model-capabilities", help="check bundled model client without inference or credentials")
    server = subcommands.add_parser("server", help="start the local Gateway server")
    server.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    server.add_argument("--port", type=local_port, default=8080)
    return parser


async def model_capabilities() -> dict:
    from importlib.metadata import version
    from src.llm.client import LLMClient, LLMConfig

    client = LLMClient(LLMConfig(base_url="http://127.0.0.1:1/v1", api_key="", model="diagnostic"))
    try:
        if await client._get_client() is None:
            raise RuntimeError("Desktop native tool client unavailable: OpenAI-compatible SDK missing")
        return {"version": 1, "native_tools_client": True, "openai": version("openai"),
                "httpx": version("httpx"), "inference_sent": False}
    finally:
        await client.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "model-capabilities":
        import asyncio
        import json
        print(json.dumps(asyncio.run(model_capabilities())))
        return 0
    if args.command == "server":
        os.environ["VORTOCODE_DESKTOP_SIDECAR"] = "1"
        from src.web.server import start_server

        start_server(args.host, args.port)
        return 0
    raise AssertionError(f"unsupported runtime command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
