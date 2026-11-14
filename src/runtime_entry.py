"""Internal service and diagnostics entry. Public interactive commands live in src.cli during migration."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from collections.abc import Sequence

from src.runtime_bootstrap import prepare_runtime
from src.utils.state_dir import state_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vortoone-runtime", description="VortoOne internal service runner"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    server = commands.add_parser("server", help="start the existing Gateway service")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8080)
    server.add_argument("--im", choices=["telegram", "dingtalk"], default="")
    im = commands.add_parser("im", help="start the existing standalone IM bridge")
    im.add_argument("channel", choices=["telegram", "dingtalk"])
    im.add_argument("--mode", choices=["plan", "build"], default="plan")
    commands.add_parser("doctor", help="run existing diagnostics (may contact configured services)")
    commands.add_parser("canary", help="run offline delivery checks")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    prepare_runtime()
    if args.command == "server":
        run_server(args.host, args.port, im=args.im)
        return 0
    if args.command == "im":
        asyncio.run(run_im(args.channel, mode=args.mode))
        return 0
    if args.command == "doctor":
        return asyncio.run(run_doctor())
    if args.command == "canary":
        return asyncio.run(run_canary_cli())
    raise AssertionError(f"unsupported internal command: {args.command}")


async def run_doctor() -> int:
    """一键自检：逐项查管道（git/凭证/中转站/serve/权限/gh/IM），返回退出码（硬伤=1）。"""
    from src.gateway.doctor import run_checks, summarize

    print("VortoOne doctor · 自检中…\n", file=sys.stderr)
    checks = await run_checks(str(Path.cwd()))
    text, code = summarize(checks)
    print(text)
    return code


async def run_canary_cli() -> int:
    """端到端交付验收：逐条报告哪条交付链路通、哪条断。有断的返回 1（部署脚本据此拦住上线）。"""
    from src.gateway.canary import run_canary, summarize

    print("VortoOne canary · 端到端交付验收（假通道 + 脚本模型，不触网）…\n", file=sys.stderr)
    lanes = await run_canary()
    for ln in lanes:
        print(f"{ln.glyph} {ln.name:<12} {ln.detail}")
    ok, text = summarize(lanes)
    print(f"\n{'✅' if ok else '⛔'} {text}")
    return 0 if ok else 1


async def run_im(channel: str, *, mode: str = "plan"):
    """IM 通道桥入口：常驻长轮询，把主 agent 搬上 IM。fail-closed：缺配对凭证直接拒启。"""
    cwd = str(Path.cwd())
    from src.gateway.im_service import (
        IMConfigError,
        build_adapter,
        load_allow_from,
        load_conversation,
        load_session_role,
    )

    try:
        adapter, owner = build_adapter(channel)  # 凭证解析/fail-closed 与 serve 内嵌同源，不抄两份
        conversation = load_conversation()
        session_role = load_session_role()
    except IMConfigError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(2)
    from src.im.bridge import IMBridge

    bridge = IMBridge(
        cwd,
        adapter,
        owner,
        channel=channel,
        mode=mode,  # standalone：自己的 runner
        allow_from=load_allow_from(channel),
        conversation=conversation,
        session_role=session_role,
    )
    who = "、".join(sorted(bridge.allow_from)) if bridge.allow_from else "空 → 拒绝一切入站"
    print(
        f"🌉 {channel} 桥启动（仓库 {Path(cwd).name}，{mode} 模式）。入站白名单：{who}；"
        f"群聊须显式 @ 本机器人。Ctrl-C 退出。",
        file=sys.stderr,
    )
    warn = bridge.allow_from_warning()  # 白名单配错（空 / 漏了 owner）→ 开机就在终端讲清楚：
    if warn:  # 不是坏了，是配置把你自己也挡在门外了
        print(warn, file=sys.stderr)
    from src.gateway.process_lease import ProcessLease

    try:
        with ProcessLease(state_dir(cwd) / "runtime.lock"):
            try:
                await bridge.run()
            finally:
                if bridge._runner is not None:
                    await bridge._runner.shutdown()
    finally:
        await adapter.close()


def run_server(host: str, port: int, im: str = ""):
    """运行服务器（im 非空 = 内嵌 IM 桥，见 gateway/im_service）"""
    from src.web.server import start_server

    start_server(host, port, im=im)


if __name__ == "__main__":
    raise SystemExit(main())
