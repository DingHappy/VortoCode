#!/usr/bin/env python3
"""VortoCode CLI 实现（既是 console_scripts 入口 vortocode/vc，也被根 main.py 复用）。"""

import argparse
import asyncio
import sys
import textwrap
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="VortoCode · 多 Agent 协作开发框架（能自分析 / 自改进自己）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            常用示例:
              %(prog)s self-analyze                       只读扫描自己、列出问题（无需 LLM key）
              %(prog)s agent "列出 src 下有哪些模块"         headless 跑主 agent（仿 claude -p；可管道/脚本化，需 key）
              %(prog)s agent -b "实现阶乘函数及其单测"       build 模式：主 agent 用隔离 dev 流水线实现（取代已退役的 run）
              %(prog)s self-improve --apply               给测试缺口自动补测试并写到新分支
              %(prog)s self-fix --paths src/foo.py        深审并外科修复指定文件
              %(prog)s server --port 8080                 启动 Web 控制台
              %(prog)s tui                                进入交互式 TUI（仿 opencode，需 .[tui]）

            每个命令都有自己的帮助，例如:  %(prog)s self-fix -h
        """),
    )
    sub = parser.add_subparsers(dest="command", metavar="<命令>",
                                title="可用命令")

    p = sub.add_parser(
        "agent",
        help="headless 主 agent：一次性跑 agent loop（仿 claude -p；可管道、可脚本化、可出 JSON）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            仿 claude -p：主 agent 与 TUI/网页同源（读码/grep/隔离 dev/受确认的 shell+开 PR），
            但无 UI、跑一回合就退，适合管道与脚本化。
            示例:
              %(prog)s "src 下有哪些模块、各做什么"          只读问答（默认 plan 模式）
              %(prog)s -b "给 foo.py 补上缺失的边界测试"      build 模式（可用 dev/写工具，隔离实现）
              %(prog)s -i shot.png "这个报错截图说明什么"     附图（mimo-v2.5 能读图）
              %(prog)s -a memo.mp3 "把这段语音转写并总结"     附音频（mimo-v2.5 能听音频）
              %(prog)s --speak "用一句话介绍这个项目"          回复合成语音 WAV（mimo-v2.5-tts）
              %(prog)s "/review src/cli.py"                  跑 .vortocode/commands/review.md 自定义命令
              %(prog)s "讲讲这个项目" && %(prog)s -c "那架构呢"   -c 续上一次对话（多轮）
              echo "审一下 cli.py 的健壮性" | %(prog)s        从 stdin 读 prompt（管道）
              %(prog)s --json "列出 src 模块" | jq .reply    JSON 输出，喂给脚本

            高危/外向操作（run_command、open_pr）headless 下默认拒绝；要放行加 --yes。
        """),
    )
    p.add_argument("prompt", nargs="?",
                   help="交给 agent 的任务/问题；省略或写成 - 时从 stdin 读")
    p.add_argument("--image", "-i", action="append", metavar="路径/URL", dest="images",
                   help="给本轮附一张图（本地路径/URL/data URL）；可重复传多张（mimo-v2.5 能读图）")
    p.add_argument("--audio", "-a", action="append", metavar="路径", dest="audio",
                   help="给本轮附一段音频（本地路径/data URL，mp3/wav 等）；可重复（mimo-v2.5 能听音频）")
    p.add_argument("--build", "-b", action="store_true",
                   help="build 模式（可用写/dev 工具，隔离实现）；默认 plan（只读/提案）")
    p.add_argument("--yes", "-y", action="store_true",
                   help="自动确认高危/外向操作（run_command/open_pr）；默认一律拒绝")
    p.add_argument("--speak", action="store_true",
                   help="把最终回复合成成语音（mimo-v2.5-tts），写 WAV；TTY 下best-effort 播放")
    p.add_argument("--voice", metavar="名称", help="--speak 的声音（可选，不同音色）")
    p.add_argument("--speak-out", metavar="路径", dest="speak_out",
                   help="语音 WAV 写到哪（默认 vorto-reply.wav）")
    p.add_argument("--model", metavar="名称",
                   help="本次用哪个模型（覆盖 .env 的 DEFAULT_MODEL），如 --model mimo-v2.5-pro")
    p.add_argument("--max-steps", type=int, metavar="N", help="覆盖单回合工具预算步数")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="以 JSON 输出 {reply, mode, tools, plan}（关闭流式）")
    p.add_argument("--continue", "-c", action="store_true", dest="continue_session",
                   help="续上一次 CLI 对话（仿 claude -c）；历史每轮落盘 .vortocode/cli_session.json")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="不在 stderr 打印工具调用/进度，只留最终输出")
    p.add_argument("--mcp", action="store_true",
                   help="连接 config/mcp.yaml 里 enabled 的 MCP 服务器，把其工具接入本回合")

    p = sub.add_parser("server", help="启动 FastAPI Web 控制台")
    p.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本地 127.0.0.1）")
    p.add_argument("--port", type=int, default=8080, help="端口（默认 8080）")

    p = sub.add_parser("analyze", help="只分析一个任务的复杂度/拆解，不执行")
    p.add_argument("--task", "-t", required=True, help="要分析的任务")

    p = sub.add_parser("self-analyze", help="[L1] 只读扫描本仓库，列出问题清单（孤儿/循环依赖/测试缺口/未声明依赖）")
    p.add_argument("--paths", help="逗号分隔的文件，额外对其跑 LLM 深审（需 key）")

    p = sub.add_parser("self-improve", help="[L2] 测试缺口 → 生成测试 → 真 pytest 门控（需 key）")
    p.add_argument("--apply", action="store_true", help="把通过门控的测试写到新分支（默认 dry-run 只看提案）")
    p.add_argument("--max-fixes", type=int, default=3, metavar="N", help="单次最多处理几个（默认 3）")

    p = sub.add_parser("self-fix", help="[L2.2] 深审 bug/坏味道 → 外科修改 → 全量门控（需 key）")
    p.add_argument("--paths", required=True, help="逗号分隔的文件，对其深审并修复")
    p.add_argument("--apply", action="store_true", help="把通过门控的修复写到新分支（默认 dry-run）")
    p.add_argument("--max-fixes", type=int, default=3, metavar="N", help="单次最多处理几个（默认 3）")

    sub.add_parser("test", help="运行测试套件（pytest）")
    sub.add_parser("demo", help="演示模式：打印指引并启动 Web 服务")
    sub.add_parser("tui", help="进入交互式全屏 TUI（仿 opencode；需 textual: pip install '.[tui]'）")

    p = sub.add_parser("im", help="IM 通道桥：常驻长连，把主 agent 搬上 IM（手机发任务/确认/回报）")
    p.add_argument("channel", choices=["telegram", "dingtalk"], help="IM 通道（telegram / dingtalk）")
    p.add_argument("--mode", choices=["plan", "build"], default="plan",
                   help="初始模式（默认 plan；IM 里可 /mode 切）")

    p = sub.add_parser("cron", help="定时作业（.vortocode/cron.yaml）：list 看表 / run <name> 手动触发一次")
    p.add_argument("action", choices=["list", "run"], help="list 列出作业 / run 手动跑一个")
    p.add_argument("name", nargs="?", help="run 时的作业名")

    p = sub.add_parser("heartbeat", help="心跳值班一次（读 .vortocode/HEARTBEAT.md + 领 BACKLOG.md）")
    p.add_argument("action", choices=["run"], help="run：立刻值班一次（隔离会话、便宜模型）")

    args = parser.parse_args()

    # 无命令：给友好总览，而不是报错
    if not args.command:
        parser.print_help()
        sys.exit(0)

    # 结构化日志（opt-in）：VORTOCODE_JSON_LOGS=1 控制台 JSON；VORTOCODE_LOG_FILE=路径 落盘（ELK-ready）
    from src.env_compat import env_compat
    if env_compat("VORTOCODE_JSON_LOGS", "AUTODEV_JSON_LOGS") \
            or env_compat("VORTOCODE_LOG_FILE", "AUTODEV_LOG_FILE"):
        from src.core.tracing import setup_structured_logging
        setup_structured_logging(log_file=env_compat("VORTOCODE_LOG_FILE", "AUTODEV_LOG_FILE") or None)

    if args.command == "agent":
        prompt = _read_prompt_arg(args.prompt)
        prompt = _maybe_expand_command(prompt, str(Path.cwd()))   # /<名> → 展开 .vortocode/commands 模板
        images = args.images or []
        audio = args.audio or []
        if not prompt and not images and not audio:
            print("agent 需要 prompt（位置参数，或从 stdin 提供）或 --image/--audio。"
                  "例：vortocode agent \"列出 src 下有哪些模块\"", file=sys.stderr)
            sys.exit(2)
        bad_i = [im for im in images if not _valid_image_ref(im)]
        if bad_i:
            print(f"以下图片找不到或不是图片：{', '.join(bad_i)}", file=sys.stderr)
            sys.exit(2)
        bad_a = [au for au in audio if not _valid_audio_ref(au)]
        if bad_a:
            print(f"以下音频找不到或不是音频：{', '.join(bad_a)}", file=sys.stderr)
            sys.exit(2)
        if not prompt and (images or audio):           # 纯附件轮：给个温和的默认指令
            prompt = "请听这段音频并转写/回答。" if audio and not images else "请看图并描述/分析其中内容。"
        asyncio.run(run_agent_headless(
            prompt, build=args.build, auto_yes=args.yes, max_steps=args.max_steps,
            as_json=args.as_json, quiet=args.quiet, images=images, audio=audio,
            speak=args.speak, voice=args.voice, speak_out=args.speak_out,
            continue_session=args.continue_session, use_mcp=args.mcp, model=args.model))

    elif args.command == "server":
        run_server(args.host, args.port)

    elif args.command == "analyze":
        asyncio.run(analyze_task(args.task))

    elif args.command == "self-analyze":
        asyncio.run(run_self_analysis(_split_paths(args.paths)))

    elif args.command == "self-improve":
        asyncio.run(run_self_improvement(apply=args.apply, max_fixes=args.max_fixes))

    elif args.command == "self-fix":
        asyncio.run(run_self_fix(_split_paths(args.paths), apply=args.apply, max_fixes=args.max_fixes))

    elif args.command == "test":
        run_tests()

    elif args.command == "demo":
        run_demo()

    elif args.command == "tui":
        run_tui()

    elif args.command == "im":
        asyncio.run(run_im(args.channel, mode=args.mode))

    elif args.command == "cron":
        asyncio.run(run_cron(args.action, args.name))

    elif args.command == "heartbeat":
        asyncio.run(run_heartbeat_cli())


async def run_im(channel: str, *, mode: str = "plan"):
    """IM 通道桥入口：常驻长轮询，把主 agent 搬上 IM。fail-closed：缺配对凭证直接拒启。"""
    import os
    cwd = str(Path.cwd())
    if channel == "telegram":
        token = os.getenv("VORTOCODE_TG_TOKEN", "").strip()
        owner = os.getenv("VORTOCODE_TG_OWNER_ID", "").strip()
        if not token or not owner:
            print("✗ Telegram 桥需要环境变量 VORTOCODE_TG_TOKEN 和 VORTOCODE_TG_OWNER_ID"
                  "（配对制，fail-closed）。\n"
                  "  ① 找 @BotFather 建 bot 拿 token；② 给 bot 发一条消息，再从 "
                  "https://api.telegram.org/bot<token>/getUpdates 读你自己的数字 chat id。",
                  file=sys.stderr)
            sys.exit(2)
        from src.im.bridge import IMBridge
        from src.im.telegram import TelegramAdapter
        adapter = TelegramAdapter(token, owner)
        bridge = IMBridge(cwd, adapter, owner, channel="telegram", mode=mode)
        print(f"🌉 Telegram 桥启动（仓库 {Path(cwd).name}，{mode} 模式）。只服务 owner "
              f"{owner}，Ctrl-C 退出。", file=sys.stderr)
        try:
            await bridge.run()
        finally:
            await adapter.close()
    elif channel == "dingtalk":
        cid = os.getenv("VORTOCODE_DD_CLIENT_ID", "").strip()
        secret = os.getenv("VORTOCODE_DD_CLIENT_SECRET", "").strip()
        owner = os.getenv("VORTOCODE_DD_OWNER_ID", "").strip()
        if not cid or not secret or not owner:
            print("✗ 钉钉桥需要环境变量 VORTOCODE_DD_CLIENT_ID / VORTOCODE_DD_CLIENT_SECRET / "
                  "VORTOCODE_DD_OWNER_ID（配对制，fail-closed）。\n"
                  "  钉钉开放平台建企业内机器人应用（Stream 模式）拿 AppKey(ClientID)/AppSecret；"
                  "OWNER_ID 填你自己的 senderStaffId（给机器人发条消息即可在回调里看到）。",
                  file=sys.stderr)
            sys.exit(2)
        from src.im.bridge import IMBridge
        from src.im.dingtalk import DingTalkAdapter
        adapter = DingTalkAdapter(cid, secret, owner)
        bridge = IMBridge(cwd, adapter, owner, channel="dingtalk", mode=mode)
        print(f"🌉 钉钉桥启动（仓库 {Path(cwd).name}，{mode} 模式）。只服务 staffId "
              f"{owner}，Ctrl-C 退出。", file=sys.stderr)
        try:
            await bridge.run()
        finally:
            await adapter.close()
    else:
        print(f"未知 IM 通道: {channel}", file=sys.stderr)
        sys.exit(2)


async def run_cron(action: str, name=None):
    """cron 子命令：list 列出作业 / run <name> 手动触发一次（隔离会话真跑，进度到 stderr）。"""
    from src.gateway import cron as _cron
    cwd = str(Path.cwd())
    if action == "list":
        jobs = _cron.load_jobs(cwd)
        if not jobs:
            print("（无 cron 作业；在 .vortocode/cron.yaml 里定义 jobs）")
            return
        state = _cron.CronState(cwd)
        for j in jobs:
            last = state.last_run(j.name)
            flag = "" if j.enabled else "（禁用）"
            print(f"· {j.name}{flag}  [{j.schedule.raw}]  announce={j.announce}  "
                  f"上次={last.isoformat(timespec='minutes') if last else '从未'}")
        return
    # action == "run"
    if not name:
        print("用法：vortocode cron run <name>", file=sys.stderr)
        sys.exit(2)
    if not any(j.name == name for j in _cron.load_jobs(cwd)):
        print(f"✗ 找不到 cron 作业 {name}（vortocode cron list 看有哪些）", file=sys.stderr)
        sys.exit(2)
    print(f"⏰ 手动触发 cron [{name}]（隔离会话）…", file=sys.stderr)
    result = await _cron.run_job_by_name(cwd, name)
    print((result or "（无输出）").strip())


async def run_heartbeat_cli():
    """heartbeat run：立刻值班一次（隔离会话、便宜模型）。领 backlog 时 submit 并**等它真跑完**。

    这是一次性命令：若领了 backlog 活就 submit 到本地 runner，然后 **drain（await）** 那个任务——
    否则 asyncio.run 退出时 pending 任务会被取消，backlog 却已被标 [~]（领走了没干完，误导，#129 评审）。
    """
    from src.gateway import heartbeat as _hb
    from src.gateway import TaskRunner
    from src.web.routers.tasks import _dev_worker
    cwd = str(Path.cwd())
    runner = TaskRunner(cwd, _dev_worker)
    submitted: list = []

    async def _submit(item):
        t = await runner.submit(item, kind="dev")
        submitted.append(t.id)                 # 记下来，函数返回前 drain，别让进程退出把它取消

    async def _notify(text):
        print(text)

    print("🫀 心跳值班一次…", file=sys.stderr)
    res = await _hb.run_heartbeat(cwd, submit=_submit, notify=_notify)
    for tid in submitted:                      # 等领到的活真正跑完（否则 backlog 标了 [~] 却没干完）
        print(f"⏳ 等后台任务 {tid} 跑完…", file=sys.stderr)
        await runner.join(tid)
        done = runner.get(tid)
        print(f"[task {tid}] {done.status if done else '?'}", file=sys.stderr)
    print(f"[heartbeat] action={res['action']}", file=sys.stderr)


def _split_paths(raw):
    """把 --paths 的逗号分隔串拆成列表；为空则 None。"""
    return [p.strip() for p in raw.split(",") if p.strip()] if raw else None


def _read_prompt_arg(raw):
    """取 headless agent 的 prompt：给了非 - 的位置参数就用它；为 - 或（省略且 stdin 是管道）则读 stdin。

    交互式终端下省略 prompt 不读 stdin（否则会挂住等输入）——交回上层报错指引。
    """
    if raw and raw != "-":
        return raw.strip()
    if raw == "-" or (raw is None and not sys.stdin.isatty()):
        try:
            return sys.stdin.read().strip()
        except Exception:  # noqa: BLE001
            return ""
    return ""


def _valid_image_ref(ref: str) -> bool:
    """图片引用是否可用：URL/data 直接放行；本地路径须存在且像图片。"""
    from src.llm.content import is_image_ref
    r = (ref or "").strip()
    if r.startswith(("data:", "http://", "https://")):
        return True
    return Path(r).expanduser().is_file() and is_image_ref(r)


def _play_audio(path: str) -> bool:
    """best-effort 播放 WAV：找到的第一个系统播放器（afplay/aplay/ffplay）后台播；找不到返回 False。"""
    import shutil
    import subprocess
    for player, flags in (("afplay", []), ("aplay", ["-q"]), ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"])):
        if shutil.which(player):
            try:
                subprocess.Popen([player, *flags, path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except Exception:  # noqa: BLE001
                return False
    return False


async def _speak_reply(text, voice, out_path, llm, quiet):
    """把回复合成成语音写到 out_path（WAV）；TTY 下顺带 best-effort 播放。返回写出的路径或 None。"""
    text = (text or "").strip()
    if not text:
        return None
    out_path = out_path or "vorto-reply.wav"
    try:
        client = llm if llm is not None else _new_llm_client()
        wav = await client.tts(text, voice=voice)
    except Exception as e:  # noqa: BLE001
        if not quiet:
            print(f"\033[2m🔊 语音合成失败：{e}\033[0m", file=sys.stderr, flush=True)
        return None
    try:
        Path(out_path).write_bytes(wav)
    except OSError as e:
        if not quiet:
            print(f"\033[2m🔊 写语音文件失败：{e}\033[0m", file=sys.stderr, flush=True)
        return None
    played = sys.stdout.isatty() and _play_audio(out_path)
    if not quiet:
        tail = "，已播放" if played else ""
        print(f"\033[2m🔊 语音已写入 {out_path}（{len(wav)} 字节{tail}）\033[0m", file=sys.stderr, flush=True)
    return out_path


def _new_llm_client():
    from src.llm.client import LLMClient
    return LLMClient()


def _valid_audio_ref(ref: str) -> bool:
    """音频引用是否可用：data:audio/ 直接放行；本地路径须存在且像音频（input_audio 不收 http URL）。"""
    from src.llm.content import is_audio_ref
    r = (ref or "").strip()
    if r.startswith("data:audio/"):
        return True
    return Path(r).expanduser().is_file() and is_audio_ref(r)


def _maybe_expand_command(prompt, cwd):
    """若 prompt 以 /<名> 开头且 <名> 是 .vortocode/commands 里的自定义命令，展开其模板；

    否则原样返回（不是命令就当普通输入）。与 TUI 的 /<名> 同源（user_commands）——
    自定义命令至此 CLI/TUI 通用，可脚本化：vortocode agent "/review src/foo.py"。
    """
    if not prompt or not prompt.startswith("/"):
        return prompt
    parts = prompt[1:].split(maxsplit=1)
    name = parts[0] if parts else ""
    cmd_args = parts[1] if len(parts) > 1 else ""
    try:
        from src.agents.user_commands import expand_command, load_commands
        uc = load_commands(cwd).get(name)
    except Exception:  # noqa: BLE001
        return prompt
    if uc is None:
        return prompt                       # 不是已知自定义命令 → 原样（当普通输入交给 agent）
    return expand_command(uc.template, cmd_args)


_MARKUP_RE = None


def _strip_markup(s: str) -> str:
    """去掉 say() 里的 rich 标记（[b]/[dim]/[/]/[#hex] 等），给纯终端 stderr 用。"""
    global _MARKUP_RE
    if _MARKUP_RE is None:
        import re
        _MARKUP_RE = re.compile(r"\[/?[a-zA-Z#][^\]]*\]|\[/\]")
    return _MARKUP_RE.sub("", s)


def _load_headless_hooks(cwd: str):
    """有 .vortocode/hooks.yaml 才建 HookSystem（与 TUI 同源，把工具生命周期事件接进 agent）。"""
    cfg = Path(cwd) / ".vortocode" / "hooks.yaml"
    if not cfg.is_file():
        return None
    try:
        from src.hooks import HookSystem
        return HookSystem(config_path=str(cfg))
    except Exception:  # noqa: BLE001
        return None


def _build_headless_agent(cwd, *, max_steps, on_tool, on_plan, confirm, llm=None, on_progress=None):
    """搭一个 headless 主 agent：工具集与网页 /agent 同源——

    只读（read_file/grep/list_files/analyze_repo）+ 隔离 dev（dev_isolated/dev_parallel，
    绿了落 vorto 分支、不碰主工作区）+ 受确认门控的 run_command/open_pr。带持久计划。
    on_progress：dev 流水线进度回调（长任务边跑边播到 stderr，免得对着静默 prompt 干等）。
    """
    from src.agents.main_agent import (MainAgent, build_agent_tools, native_default,
                                        skill_catalog)
    from src.agents.permissions import load_permissions
    from src.agents.project import load_project_instructions
    # 与 Web /agent 共用同一工具装配（build_agent_tools），保证"同源"、不漂移；
    # headless 无浏览器 → 不含制品工具（with_artifacts=False）。
    tools = build_agent_tools(cwd, confirm=confirm, on_progress=on_progress, with_artifacts=False)
    kwargs = {"plan_tool": True, "on_tool": on_tool, "on_plan": on_plan,
              "permissions": load_permissions(cwd), "env_context": True,   # 注入 <env>（cwd/git/日期/目录）
              "native": native_default()}      # 三端统一 native 开关（此前 CLI 忽略 VORTOCODE_NATIVE_TOOLS）
    parts = []
    proj = load_project_instructions(cwd)              # AGENTS.md/CLAUDE.md 项目约定进系统提示
    if proj:
        parts.append(proj)
    catalog = skill_catalog(cwd)                       # 技能目录进系统提示（模型才知道有哪些技能可 use_skill）
    if catalog:
        parts.append(f"【可用技能】(需要时用 use_skill 加载其完整指令再执行)\n{catalog}")
    if parts:
        kwargs["extra_system"] = "\n\n".join(parts)
    if max_steps:
        kwargs["max_steps"] = max_steps
    if llm is not None:
        kwargs["llm"] = llm
    hooks = _load_headless_hooks(cwd)
    if hooks is not None:
        kwargs["hook_system"] = hooks
    return MainAgent(tools, **kwargs)


async def run_agent_headless(prompt, *, build=False, auto_yes=False, max_steps=None,
                             as_json=False, quiet=False, llm=None, images=None, audio=None,
                             speak=False, voice=None, speak_out=None, continue_session=False,
                             use_mcp=False, model=None):
    """headless 跑一回合主 agent loop（仿 claude -p）：无 UI、跑完即返回。

    输出契约：最终回复 → stdout；工具调用/进度 → stderr（--quiet 静默）。
    TTY 且非 --json 时把回复流式写 stdout；管道/重定向/--json 则一次性输出（利于脚本/jq）。
    高危/外向工具（run_command/open_pr）默认拒绝，--yes 才放行（headless 无人值守，安全优先）。
    images: 可选图片引用列表（路径/URL/data URL），挂到本轮 user 消息（mimo-v2.5 能读图）。
    audio:  可选音频引用列表（路径/data URL），挂到本轮 user 消息（mimo-v2.5 能听音频）。
    speak:  True 则把最终回复合成成语音写 WAV（speak_out，默认 vorto-reply.wav）、TTY 下试播。
    continue_session: True 则续上上一次 CLI 对话（仿 claude -c）——把存盘历史灌回 agent；
                每轮结束都把历史落盘（.vortocode/cli_session.json），所以下次 -c 能接上。
    """
    import os
    cwd = os.getcwd()
    mode = "build" if build else "plan"

    tools_log: list = []
    plan_holder = {"plan": []}

    def _on_tool(name, args, result):
        tools_log.append({"tool": name, "args": args})

    def _on_plan(plan):
        plan_holder["plan"] = plan

    async def _confirm(message):
        first = (message or "").splitlines()[0] if message else ""
        if auto_yes:
            if not quiet:
                print(f"\033[2m✓ 自动确认：{first}\033[0m", file=sys.stderr, flush=True)
            return True
        if not quiet:
            print(f"\033[2m✗ 自动拒绝（需 --yes 放行）：{first}\033[0m", file=sys.stderr, flush=True)
        return False

    def _progress(msg):                           # dev 流水线进度 → stderr（--quiet 静默）
        if not quiet:
            print(f"\033[2m{msg}\033[0m", file=sys.stderr, flush=True)

    agent = _build_headless_agent(cwd, max_steps=max_steps, on_tool=_on_tool,
                                  on_plan=_on_plan, confirm=_confirm, llm=llm, on_progress=_progress)
    if model:                                     # --model：本次覆盖 .env 的 DEFAULT_MODEL
        try:
            agent.set_model(model)
            if not quiet:
                print(f"\033[2m🧠 用模型 {model}\033[0m", file=sys.stderr, flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"设置模型失败: {e}", file=sys.stderr)
    if continue_session:                          # 续上一次 CLI 对话（claude -c 式）
        hist = _load_cli_history(cwd)
        if hist:
            agent.history = hist
            if not quiet:
                print(f"\033[2m↩ 续上上次对话（{len(hist)} 条历史）\033[0m", file=sys.stderr, flush=True)

    mcp_mgr = None
    if use_mcp:                                   # --mcp：接 config/mcp.yaml 的 MCP 服务器工具
        from src.agents.mcp_tools import connect_mcp
        try:
            mcp_mgr, mcp_tools = await connect_mcp(cwd)
            if mcp_tools:
                agent.add_tools(mcp_tools)
                if not quiet:
                    print(f"\033[2m🔌 接入 {len(mcp_tools)} 个 MCP 工具\033[0m", file=sys.stderr, flush=True)
            elif not quiet:
                print("\033[2m🔌 无 MCP 工具（config/mcp.yaml 缺失或无 enabled 服务器）\033[0m",
                      file=sys.stderr, flush=True)
        except Exception as e:  # noqa: BLE001
            if not quiet:
                print(f"\033[2m🔌 MCP 连接失败: {e}\033[0m", file=sys.stderr, flush=True)

    def say(markup):
        if quiet:
            return
        print(f"\033[2m{_strip_markup(markup)}\033[0m", file=sys.stderr, flush=True)

    streaming = (not as_json) and sys.stdout.isatty()
    seen = {"n": 0}                                # stream_cb 给的是累计文本，按长度算增量

    def stream_cb(text):
        delta = text[seen["n"]:]
        if delta:
            sys.stdout.write(delta)
            sys.stdout.flush()
            seen["n"] = len(text)

    def reason_cb(delta):                          # 思考呈现：思维链 → stderr(dim)，不进 stdout/json；--quiet 静默
        if quiet:
            return
        sys.stderr.write(f"\033[2m{delta}\033[0m")
        sys.stderr.flush()

    if not quiet and (images or audio):
        bits = []
        if images:
            bits.append(f"🖼 {len(images)} 张图")
        if audio:
            bits.append(f"🎧 {len(audio)} 段音频")
        print(f"\033[2m附带 {' · '.join(bits)}\033[0m", file=sys.stderr, flush=True)
    reply = await agent.run_turn(
        prompt, mode=mode, say=say, emit=(lambda _m: None),
        stream_cb=(stream_cb if streaming else None), images=images, audio=audio,
        reasoning_cb=reason_cb)

    if as_json:
        import json
        print(json.dumps({"reply": reply, "mode": mode,
                          "tools": tools_log, "plan": plan_holder["plan"]},
                         ensure_ascii=False, indent=2))
    elif streaming:
        if seen["n"] == 0:                         # 没流出任何东西（空回复/出错）→ 兜底打印
            print(reply)
        elif not reply.endswith("\n"):
            print()                                # 给流式文本补个换行
    else:
        print(reply)                               # 管道/重定向：一次性输出
    if speak and reply:                            # 语音回复：把最终文字合成成 WAV（mimo-v2.5-tts）
        await _speak_reply(reply, voice, speak_out, llm, quiet)
    _save_cli_history(cwd, getattr(agent, "history", []))   # 落盘，供下次 --continue 接上
    if mcp_mgr is not None:                        # 关掉 MCP 子进程，别残留
        try:
            await mcp_mgr.shutdown()
        except Exception:  # noqa: BLE001
            pass
    return reply


_CLI_SESSION = ".vortocode/cli_session.json"


def _load_cli_history(cwd):
    """读回上次 CLI 对话历史（list）；不存在/坏文件 → []。"""
    import json
    p = Path(cwd) / _CLI_SESSION
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data.get("history", []) if isinstance(data, dict) else []


def _save_cli_history(cwd, history):
    """把 CLI 对话历史落盘（尾 40 条；多模态 content 折成纯文本，免 base64 撑爆文件）。失败安全吞。"""
    import json

    from src.llm.content import content_to_text
    out = []
    for m in list(history or [])[-40:]:
        c = m.get("content")
        out.append({"role": m.get("role", ""),
                    "content": c if isinstance(c, str) else content_to_text(c)})
    p = Path(cwd) / _CLI_SESSION
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"history": out}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except (OSError, TypeError, ValueError):
        pass


async def _with_progress(coro, label: str = "运行中"):
    """跑 coro，运行期间在 stderr 单行刷新「⏳ label Ns…」，让人知道没卡死；结束即清行。

    长跑命令（开发流水线 / 自分析 / 自改进）此前打个标题就闷头跑，看不出是否在动。
    非 TTY（管道 / 重定向 / 日志）则完全静默、不污染输出。
    """
    import time as _time
    if not sys.stderr.isatty():
        return await coro
    t0 = _time.monotonic()
    done = asyncio.Event()

    async def _ticker():
        while not done.is_set():
            el = int(_time.monotonic() - t0)
            print(f"\r\033[2m⏳ {label} {el}s…\033[0m", end="", file=sys.stderr, flush=True)
            try:
                await asyncio.wait_for(done.wait(), 1.0)
            except asyncio.TimeoutError:
                pass

    tick = asyncio.create_task(_ticker())
    try:
        return await coro
    finally:
        done.set()
        await tick
        print("\r\033[K", end="", file=sys.stderr, flush=True)   # 清掉计时行，给真正的结果让位


def run_server(host: str, port: int):
    """运行服务器"""
    from src.web.server import start_server
    start_server(host, port)


async def analyze_task(task: str):
    """分析任务"""
    from src.orchestrator import TaskAnalyzer
    
    analyzer = TaskAnalyzer()
    analysis = await _with_progress(analyzer.analyze(task), "分析任务")
    
    print(f"Task: {task}")
    print(f"Complexity: {analysis.complexity}")
    print(f"Effort: {analysis.estimated_effort}")
    print(f"Duration: {analysis.estimated_duration}s")
    print(f"Capabilities: {analysis.required_capabilities}")
    print(f"Agents: {analysis.suggested_agents}")


async def run_self_analysis(paths=None):
    """L1 自我分析：扫描本仓库，打印排序的问题清单（只读）。

    paths 给定时（如 --paths src/a.py,src/b.py）才对这些文件跑 LLM 深审。
    """
    from src.orchestrator.self_analysis import analyze_self, render_report

    if paths:
        print(f"对 {len(paths)} 个文件启用 LLM 深审：{', '.join(paths)}")
    report = await _with_progress(analyze_self(".", llm_paths=paths), "扫描仓库")
    print(render_report(report))


async def run_self_improvement(apply: bool = False, max_fixes: int = 3):
    """L2 自我改进：L1 找测试缺口 -> 生成测试 -> 真 pytest 门控 -> 提案（默认 dry-run）。

    需要配置 LLM（用于生成测试）。--apply 时把通过门控的测试写到一个新分支供 review。
    """
    from src.orchestrator.self_improve import SelfImprovementLoop, render_result

    loop = SelfImprovementLoop(".", max_fixes=max_fixes)
    result = await _with_progress(loop.propose(), "生成并门控测试")
    if apply and result.accepted:
        branch = loop.apply(result)
        print(f"已把 {len(result.accepted)} 个通过门控的测试写到分支: {branch}")
    print(render_result(result))


async def run_self_fix(paths=None, apply: bool = False, max_fixes: int = 3):
    """L2.2 代码修复：深审找 bug/坏味道 -> 外科修改 -> 全量测试门控 -> 提案（默认 dry-run）。

    需要配置 LLM（深审与编辑都用）。--paths 指定要审+修的文件。
    --apply 时把通过全量门控的修复写到新分支供 review。
    """
    from src.orchestrator.self_analysis import analyze_self
    from src.orchestrator.code_fix import CodeFixLoop, render_result

    if not paths:
        print("self-fix 需要 --paths 指定文件，如：--paths src/a.py,src/b.py")
        return

    print(f"深审 {len(paths)} 个文件以发现 bug/坏味道：{', '.join(paths)}")
    report = await _with_progress(analyze_self(".", llm_paths=paths), "深审文件")
    loop = CodeFixLoop(".", max_fixes=max_fixes)
    result = await _with_progress(loop.propose(report.findings), "修复并门控")   # 内部只挑 bug/code-smell 类
    if apply and result.accepted:
        branch = loop.apply(result)
        print(f"已把 {len(result.accepted)} 个通过门控的修复写到分支: {branch}")
    print(render_result(result))


def run_tui():
    """启动交互式 TUI（仿 opencode）。未装 textual 时给出安装提示，不崩。"""
    # 中文/输入法(IME)输入修复：禁用 Kitty 键盘协议。
    # textual 8.x 启用 Kitty 协议时会带上「关联文本上报」标志(\x1b[>25u)，但它自己的
    # CSI u 解析器(textual/_xterm_parser.py)处理不了输入法一次性提交的多码点中文——
    # 形如 \x1b[32;;20320:22909u（“你好”）：正则不接受冒号子参数、chr(int(text)) 也只认
    # 单码点，于是整段转义序列被当成普通文字漏进输入框（显示成 [32;;20320:22909u）。
    # 关掉后终端回退传统编码，IME 中文以普通 UTF-8 字符到达，输入恢复正常。
    # 必须在 import textual 之前设置（textual.constants 在导入时读取该变量）。
    # 仍想用 Kitty 协议者可显式覆盖：export TEXTUAL_DISABLE_KITTY_KEY=0
    import os
    os.environ.setdefault("TEXTUAL_DISABLE_KITTY_KEY", "1")
    try:
        from src.tui.app import run as run_tui_app
    except ImportError as e:
        if (getattr(e, "name", "") or "") in ("textual", "rich") or "textual" in str(e) or "rich" in str(e):
            print("交互式 TUI 需要 textual：请先  pip install '.[tui]'（或 pip install textual rich）")
            return
        raise
    run_tui_app()


def run_tests():
    """运行测试"""
    import subprocess
    subprocess.run([sys.executable, "-m", "pytest", "tests/", "-v"])


def run_demo():
    """运行演示"""
    print("""
╔══════════════════════════════════════════════════════════════╗
║                 VortoCode 演示模式                        ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  1. 启动 Web 服务器:                                         ║
║     python main.py server                                    ║
║                                                              ║
║  2. 打开浏览器访问:                                          ║
║     http://localhost:8000                                    ║
║                                                              ║
║  3. 输入开发目标，点击"开始执行"                              ║
║                                                              ║
║  4. 观察 Agent 协作完成任务                                  ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
    """)
    
    # 自动启动服务器（默认仅绑本地；对外暴露请显式传 --host 并设置 VORTOCODE_API_TOKEN）
    run_server("127.0.0.1", 8000)


if __name__ == "__main__":
    main()
