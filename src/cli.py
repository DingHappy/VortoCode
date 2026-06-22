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
              %(prog)s run -t "实现阶乘函数及其单测"        跑完整开发流水线（需 key）
              %(prog)s self-improve --apply               给测试缺口自动补测试并写到新分支
              %(prog)s self-fix --paths src/foo.py        深审并外科修复指定文件
              %(prog)s server --port 8080                 启动 Web 控制台
              %(prog)s quant run --mock                   量化流水线（mock 数据）
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
    p.add_argument("--max-steps", type=int, metavar="N", help="覆盖单回合工具预算步数")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="以 JSON 输出 {reply, mode, tools, plan}（关闭流式）")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="不在 stderr 打印工具调用/进度，只留最终输出")

    p = sub.add_parser("run", help="跑完整多 Agent 开发流水线（产品→架构→开发→审查→测试）")
    p.add_argument("--task", "-t", required=True, help="要实现的开发目标，如 “实现用户登录接口”")

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

    p = sub.add_parser("quant", help="量化研究流水线（新闻/情绪/因子/复盘）",
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", nargs="?", default="run",
                   choices=["run", "cli", "server", "check", "results"],
                   help="run=跑流水线(默认)  cli=交互仪表盘  server=带量化API的Web  check=环境自检  results=查历史")
    p.add_argument("--stage", choices=["news", "sentiment", "factor", "review", "all"],
                   default="all", help="run 模式跑哪个阶段（默认 all）")
    p.add_argument("--interval", type=int, default=0, help="覆盖各阶段间隔（秒）")
    p.add_argument("--mock", action="store_true", help="用 mock 数据，无需外部 quant-platform")
    p.add_argument("--host", default="127.0.0.1", help="server 模式监听地址")
    p.add_argument("--port", type=int, default=8080, help="server 模式端口")

    sub.add_parser("test", help="运行测试套件（pytest）")
    sub.add_parser("demo", help="演示模式：打印指引并启动 Web 服务")
    sub.add_parser("tui", help="进入交互式全屏 TUI（仿 opencode；需 textual: pip install '.[tui]'）")

    args = parser.parse_args()

    # 无命令：给友好总览，而不是报错
    if not args.command:
        parser.print_help()
        sys.exit(0)

    # 结构化日志（opt-in）：AUTODEV_JSON_LOGS=1 控制台 JSON；AUTODEV_LOG_FILE=路径 落盘（ELK-ready）
    import os as _os
    if _os.getenv("AUTODEV_JSON_LOGS") or _os.getenv("AUTODEV_LOG_FILE"):
        from src.core.tracing import setup_structured_logging
        setup_structured_logging(log_file=_os.getenv("AUTODEV_LOG_FILE") or None)

    if args.command == "agent":
        prompt = _read_prompt_arg(args.prompt)
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
            speak=args.speak, voice=args.voice, speak_out=args.speak_out))

    elif args.command == "run":
        asyncio.run(run_task(args.task))

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

    elif args.command == "quant":
        if args.mode == "check":
            from src.orchestrator.quant_check import check_environment
            asyncio.run(check_environment())
        elif args.mode == "results":
            asyncio.run(show_quant_results())
        elif args.mode == "server":
            run_server(args.host, args.port)
        elif args.mode == "cli":
            asyncio.run(run_quant_cli())
        else:  # run
            asyncio.run(run_quant(args.stage, args.interval, mock=args.mock))


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


def _build_headless_agent(cwd, *, max_steps, on_tool, on_plan, confirm, llm=None):
    """搭一个 headless 主 agent：工具集与网页 /agent 同源——

    只读（read_file/grep/list_files/analyze_repo）+ 隔离 dev（dev_isolated/dev_parallel，
    绿了落 vorto 分支、不碰主工作区）+ 受确认门控的 run_command/open_pr。带持久计划。
    """
    from src.agents.main_agent import (MainAgent, build_command_tool,
                                       build_dev_tools, build_pr_tool, build_read_tools)
    tools = (build_read_tools(cwd) + build_dev_tools(cwd)
             + build_command_tool(cwd, confirm) + build_pr_tool(cwd, confirm))
    kwargs = {"plan_tool": True, "on_tool": on_tool, "on_plan": on_plan}
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
                             speak=False, voice=None, speak_out=None):
    """headless 跑一回合主 agent loop（仿 claude -p）：无 UI、跑完即返回。

    输出契约：最终回复 → stdout；工具调用/进度 → stderr（--quiet 静默）。
    TTY 且非 --json 时把回复流式写 stdout；管道/重定向/--json 则一次性输出（利于脚本/jq）。
    高危/外向工具（run_command/open_pr）默认拒绝，--yes 才放行（headless 无人值守，安全优先）。
    images: 可选图片引用列表（路径/URL/data URL），挂到本轮 user 消息（mimo-v2.5 能读图）。
    audio:  可选音频引用列表（路径/data URL），挂到本轮 user 消息（mimo-v2.5 能听音频）。
    speak:  True 则把最终回复合成成语音写 WAV（speak_out，默认 vorto-reply.wav）、TTY 下试播。
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

    agent = _build_headless_agent(cwd, max_steps=max_steps, on_tool=_on_tool,
                                  on_plan=_on_plan, confirm=_confirm, llm=llm)

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

    if not quiet and (images or audio):
        bits = []
        if images:
            bits.append(f"🖼 {len(images)} 张图")
        if audio:
            bits.append(f"🎧 {len(audio)} 段音频")
        print(f"\033[2m附带 {' · '.join(bits)}\033[0m", file=sys.stderr, flush=True)
    reply = await agent.run_turn(
        prompt, mode=mode, say=say, emit=(lambda _m: None),
        stream_cb=(stream_cb if streaming else None), images=images, audio=audio)

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
    return reply


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


async def run_task(task: str):
    """运行任务"""
    from src.orchestrator import create_default_engine

    print(f"Running task: {task}")
    print("=" * 50)

    # 初始化引擎（注册全部 5 个角色 Agent：product/architect/developer/reviewer/tester）
    engine = await create_default_engine()

    # 执行任务
    result = await _with_progress(engine.orchestrate(task), "开发流水线")
    
    print(f"\nResult: {'Success' if result.success else 'Failed'}")
    print(f"Duration: {result.duration:.2f}s")
    print(f"Subtasks: {len(result.results)}")
    
    if result.error:
        print(f"Error: {result.error}")


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
    
    # 自动启动服务器（默认仅绑本地；对外暴露请显式传 --host 并设置 AUTODEV_API_TOKEN）
    run_server("127.0.0.1", 8000)


async def run_quant(stage: str = "all", interval: int = 0, mock: bool = False):
    """运行量化研究流水线"""
    from src.orchestrator.quant_pipeline import create_quant_pipeline

    intervals = {}
    if interval > 0:
        for s in ("news", "sentiment", "factor", "review"):
            intervals[s] = interval

    if mock:
        print("\033[33m[Mock 模式] 使用模拟数据，无需 quant-platform\033[0m")

    print("启动量化研究流水线...")
    pipeline = await create_quant_pipeline(intervals=intervals or None, mock=mock)

    if stage == "all":
        await pipeline.start_all()
        print("所有流水线已启动，按 Ctrl+C 停止。")
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\n正在停止...")
            await pipeline.stop_all()
    else:
        stage_names = {
            "news": "新闻采集", "sentiment": "情绪分析",
            "factor": "因子研究", "review": "每日复盘",
        }
        stage_map = {
            "news": pipeline._run_news_collection,
            "sentiment": pipeline._run_sentiment_analysis,
            "factor": pipeline._run_factor_research,
            "review": pipeline._run_daily_review,
        }
        func = stage_map[stage]
        print(f"\n{'─' * 50}")
        print(f"  执行阶段: {stage_names.get(stage, stage)}")
        print(f"{'─' * 50}")

        import time as _time
        t0 = _time.time()
        try:
            result = await func()
            elapsed = _time.time() - t0
            success = getattr(result, "success", False)
            icon = "\033[32m✓\033[0m" if success else "\033[31m✗\033[0m"
            print(f"\n  {icon} 结果: {'成功' if success else '失败'}  ({elapsed:.1f}s)")

            # 展示结果摘要
            output = getattr(result, "output", None)
            results_list = getattr(result, "results", [])
            if output:
                _print_result_summary(stage, output)
            elif results_list:
                for r in results_list[:3]:
                    role = r.get("role", "?")
                    ok = r.get("success", False)
                    icon = "\033[32m✓\033[0m" if ok else "\033[31m✗\033[0m"
                    print(f"  {icon} {role}")

            # 保存到记忆
            await pipeline._on_news_collected(result) if stage == "news" else None
            await pipeline._on_sentiment_done(result) if stage == "sentiment" else None
            await pipeline._on_factor_done(result) if stage == "factor" else None
            await pipeline._on_review_done(result) if stage == "review" else None

        except Exception as e:
            elapsed = _time.time() - t0
            print(f"\n  \033[31m✗ 执行失败 ({elapsed:.1f}s)\033[0m")
            print(f"  错误: {e}")

        print(f"{'─' * 50}\n")


def _print_result_summary(stage: str, output):
    """打印结果摘要"""
    import json as _json

    if stage == "review":
        # 复盘是 Markdown 文本，打印前 20 行
        lines = str(output).split("\n")[:20]
        for line in lines:
            print(f"  {line}")
        if len(str(output).split("\n")) > 20:
            print(f"  ... (共 {len(str(output).split(chr(10)))} 行)")
    elif isinstance(output, dict):
        # 结构化数据，打印关键字段
        for key, val in output.items():
            if isinstance(val, (list, dict)):
                count = len(val) if isinstance(val, list) else len(val.keys())
                print(f"  {key}: ({count} 项)")
            else:
                print(f"  {key}: {val}")
    elif isinstance(output, list):
        print(f"  共 {len(output)} 条结果")
        for item in output[:5]:
            if isinstance(item, dict):
                summary = item.get("summary", item.get("target", str(item)[:80]))
                sentiment = item.get("sentiment", "")
                print(f"  - {summary} {sentiment}")
    else:
        text = str(output)[:500]
        print(f"  {text}")


async def show_quant_results():
    """查看量化记忆中的历史结果"""
    from src.memory.base import MemorySystem

    memory = MemorySystem()
    stages = [
        ("新闻采集", "observation"),
        ("情绪分析", "thought"),
        ("因子研究", "result"),
        ("每日复盘", "result"),
    ]

    print(f"\n{'═' * 60}")
    print(f"  量化历史结果")
    print(f"{'═' * 60}")

    for name, mem_type in stages:
        items = await memory.retrieve(name, top_k=3)
        print(f"\n  {name}:")
        if not items:
            print(f"    (无记录)")
        for item in items:
            ts = item.timestamp.strftime("%m-%d %H:%M") if hasattr(item, "timestamp") else "?"
            content = item.content[:120].replace("\n", " ")
            print(f"    [{ts}] {content}...")

    print(f"\n{'═' * 60}\n")


async def run_quant_cli():
    """启动量化交互式 CLI"""
    from src.orchestrator.quant_pipeline import create_quant_pipeline
    from src.orchestrator.quant_cli import run_quant_cli as _cli

    pipeline = await create_quant_pipeline()
    await _cli(pipeline)


if __name__ == "__main__":
    main()
