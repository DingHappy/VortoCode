#!/usr/bin/env python3
"""VortoCode CLI 实现（既是 console_scripts 入口 vortocode/vc，也被根 main.py 复用）。"""

import argparse
import asyncio
import sys
import textwrap
from pathlib import Path


def _resolve_log_level(raw) -> int:
    """把 ``LOG_LEVEL`` 的原始值解析成 logging 级别；**认不出一律 WARNING**。

    抽成纯函数是为了能确定性地测"没设置""拼错了"这些分支——``_setup_logging`` 会去读
    仓库根的 ``.env``，在开发机上"未设置"这个分支根本构造不出来。

    写错不能让进程起不来：日志级别拼错就炸掉整个服务是荒谬的。
    """
    import logging

    level = getattr(logging, str(raw or "WARNING").strip().upper(), None)
    return level if isinstance(level, int) else logging.WARNING


def _setup_logging() -> int:
    """按 ``LOG_LEVEL`` 配日志。**不设则 WARNING**——与今天的实际行为逐字节一致。

    为什么非有这段不可：全仓**一处日志配置都没有**。根 logger 没 handler，Python 就回落到
    ``logging.lastResort``，而那个只处理 WARNING 及以上。后果是全仓 60 处 ``.info()``
    一行都产不出来，同时 ``.env`` 里那个 ``LOG_LEVEL`` **没有任何代码读它**，纯摆设。

    于是日志里**只有失败、没有成功**。2026-08-01 真机排查"点了按钮没反应"时就卡死在这：
    加了成功日志照样一片空白，分不清是"回调没到"还是"到了但没效果"——而这两者修法完全不同。

    返回实际生效的级别（int），好让测试断解析结果而不必依赖全局 logging 状态。
    """
    import logging
    import os

    # LOG_LEVEL 写在 .env 里，而 .env 要等 src.llm.client 被导入才加载——这里显式再读一次。
    # （load_dotenv 默认不覆盖已存在的环境变量，重复调用无害。）
    try:
        from dotenv import load_dotenv

        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass

    level = _resolve_log_level(os.getenv("LOG_LEVEL"))
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if level > logging.DEBUG:
        # 第三方库一到 INFO 就每个 HTTP 请求刷一行，会把自己的日志淹得找不着
        for noisy in ("aiohttp", "asyncio", "httpx", "httpcore", "openai", "urllib3",
                      "websockets", "charset_normalizer"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    return level


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
                   help="续上一次 CLI 对话（仿 claude -c）；历史每轮落盘 .vortocode/cli_session.json"
                        "（--attach 下续的是 serve 侧的 cli 会话）")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="不在 stderr 打印工具调用/进度，只留最终输出")
    p.add_argument("--mcp", action="store_true",
                   help="连接 config/mcp.yaml 里 enabled 的 MCP 服务器，把其工具接入本回合")
    p.add_argument("--capabilities", choices=["local", "external"], metavar="PROFILE",
                   help="会话能力：local=开发/凭据且禁 Web/MCP；external=外部内容且无宿主凭据（默认 local）")
    p.add_argument("--attach", nargs="?", const="", metavar="URL",
                   help="把回合交给常驻 serve 跑（协议客户端模式）：连 URL 的 /ws"
                        "（缺省 $VORTOCODE_SERVE_URL 或 http://127.0.0.1:8080）；"
                        "serve 不在则自动回退进程内执行")

    p = sub.add_parser("server", help="启动 FastAPI Web 控制台")
    p.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本地 127.0.0.1）")
    p.add_argument("--port", type=int, default=8080, help="端口（默认 8080）")
    p.add_argument("--im", choices=["telegram", "dingtalk"], default="",
                   help="内嵌 IM 桥（单进程唯一状态所有者，共享任务池；凭证缺失拒启）")

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
    p = sub.add_parser("tui", help="进入交互式全屏 TUI（仿 opencode；需 textual: pip install '.[tui]'）")
    p.add_argument("--attach", nargs="?", const="", metavar="URL",
                   help="协议客户端模式：回合交常驻 serve 跑（缺省 $VORTOCODE_SERVE_URL 或 "
                        "http://127.0.0.1:8080）；serve 不在则该回合自动回退进程内")

    p = sub.add_parser("im", help="IM 通道桥：常驻长连，把主 agent 搬上 IM（手机发任务/确认/回报）")
    p.add_argument("channel", choices=["telegram", "dingtalk"], help="IM 通道（telegram / dingtalk）")
    p.add_argument("--mode", choices=["plan", "build"], default="plan",
                   help="初始模式（默认 plan；IM 里可 /mode 切）")

    p = sub.add_parser("cron", help="定时作业（.vortocode/cron.yaml）：list 看表 / run <name> 手动触发一次")
    p.add_argument("action", choices=["list", "run"], help="list 列出作业 / run 手动跑一个")
    p.add_argument("name", nargs="?", help="run 时的作业名")

    p = sub.add_parser("collect", help="信号采集：抓 HN/GitHub/RSS/V2EX，落一条 signals 产出物"
                                      "（确定性、零 LLM，给 cron 的 command: 档用）")
    p.add_argument("-q", "--quiet", action="store_true", help="只报结果，不列条目")

    p = sub.add_parser("stats", help="渠道数据查询：把已登记的发布链接查一遍，落一条 channel_stats")
    p.add_argument("-q", "--quiet", action="store_true", help="只报结果，不列条目")

    p = sub.add_parser("pipeline", help="作业流水线（.vortocode/pipelines/*.yaml）：非代码流程的工序 + 人批闸门")
    p.add_argument("action", choices=["list", "show", "start", "advance", "review", "tick"],
                   help="list 看全部 / show <run> 看细节 / start <名字> 开一轮 / "
                        "advance <run> 推一道工序 / review <run> 给人批回执 / "
                        "tick 无人值守扫一遍（给 cron 用，状态有变化才通报）")
    p.add_argument("name", nargs="?", default="",
                   help="run id（show/advance/review）、流水线名（start，或 tick 时只扫这一条）")
    p.add_argument("--approve", dest="verdict", action="store_const", const="approve",
                   help="review：放行，下次 advance 继续")
    p.add_argument("--reject", dest="verdict", action="store_const", const="reject",
                   help="review：驳回重做（意见落成产出物进血缘，旧版不删）")
    p.add_argument("--defer", dest="verdict", action="store_const", const="defer",
                   help="review：挂起——不推进也不失败")
    p.add_argument("-m", "--comment", default="", help="人批意见（驳回时会进血缘）")
    p.add_argument("--rollback-to", default="",
                   help="驳回时退回哪道工序（默认退回当前这道）；退回点由你指定，不让模型猜")
    p.add_argument("--max-stages", type=int, default=1,
                   help="一次最多推几道工序（默认 1——每道都是一次真 LLM 回合）")
    p.add_argument("--yes", action="store_true",
                   help="给 outbound 工序放行。不给这个标志时对外动作一律 fail-closed 拒绝")
    p.add_argument("--if-idle", action="store_true",
                   help="start 时：已有未完成的运行就不开新的（定时开轮必备，防堆积）")

    p = sub.add_parser("heartbeat", help="心跳值班一次（读 .vortocode/HEARTBEAT.md + 领 BACKLOG.md）")
    p.add_argument("action", choices=["run"], help="run：立刻值班一次（隔离会话、便宜模型）")

    sub.add_parser("doctor", help="一键自检：git/凭证/中转站/serve/权限文件/gh/IM——常驻化后'用不了'大多是管道问题")

    sub.add_parser("canary", help="端到端交付验收：一句话进来→确认→结果送到出站面，六条链路逐条验"
                                  "（不触网/不烧 token/不碰真工作区；部署后的强制关卡）")

    p = sub.add_parser("roster", help="多助手名册（每人一个专属小蜜）：list 看名单 / check 体检 / "
                                      "template 出 env 模板——起服务前先查出配错的地方")
    p.add_argument("action", choices=["list", "check", "template"],
                   help="list 列出名册 / check 体检（凭证复用、白名单漏人、权限过松…）/ "
                        "template <通道> 打印 env 模板")
    p.add_argument("arg", nargs="?", help="template 时的通道名（dingtalk / telegram）")
    p.add_argument("--file", dest="roster_file", default="",
                   help="名册路径（默认 .vortocode/assistants.yaml）")

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
    else:
        _setup_logging()      # 没开结构化日志时的兜底配置（否则整个进程只剩 WARNING，见其 docstring）

    if args.command == "agent":
        # argparse 的 --attach [URL]（nargs="?"）会把紧跟的任务文本吞成 URL：
        # `vc agent --attach "修个 bug"` → attach="修个 bug"、prompt 空。按"像不像 URL"消歧回来。
        args.prompt, args.attach = _disambiguate_attach(args.prompt, args.attach)
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
        try:
            asyncio.run(run_agent_headless(
                prompt, build=args.build, auto_yes=args.yes, max_steps=args.max_steps,
                as_json=args.as_json, quiet=args.quiet, images=images, audio=audio,
                speak=args.speak, voice=args.voice, speak_out=args.speak_out,
                continue_session=args.continue_session, use_mcp=args.mcp, model=args.model,
                attach=args.attach, capability_profile=args.capabilities))
        except KeyboardInterrupt:              # Ctrl-C：WS 断开即中断 serve 侧回合（disconnect 兜底）
            sys.exit(130)

    elif args.command == "server":
        run_server(args.host, args.port, im=args.im)

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
        run_tui(attach=args.attach)

    elif args.command == "im":
        asyncio.run(run_im(args.channel, mode=args.mode))

    elif args.command == "cron":
        asyncio.run(run_cron(args.action, args.name))

    elif args.command == "collect":
        from src.gateway.collect_cli import run_collect_cli
        sys.exit(run_collect_cli(quiet=args.quiet))

    elif args.command == "stats":
        from src.gateway.collect_cli import run_stats_cli
        sys.exit(run_stats_cli(quiet=args.quiet))

    elif args.command == "pipeline":
        from src.gateway.pipeline_cli import run_pipeline_cli
        sys.exit(asyncio.run(run_pipeline_cli(
            args.action, args.name, verdict=args.verdict or "", comment=args.comment,
            rollback_to=args.rollback_to, max_stages=args.max_stages, yes=args.yes,
            if_idle=args.if_idle)))

    elif args.command == "heartbeat":
        asyncio.run(run_heartbeat_cli())

    elif args.command == "doctor":
        sys.exit(asyncio.run(run_doctor()))

    elif args.command == "canary":
        sys.exit(asyncio.run(run_canary_cli()))

    elif args.command == "roster":
        sys.exit(run_roster_cli(args.action, args.arg or "", args.roster_file))


async def run_doctor() -> int:
    """一键自检：逐项查管道（git/凭证/中转站/serve/权限/gh/IM），返回退出码（硬伤=1）。"""
    from src.gateway.doctor import run_checks, summarize
    print("VortoCode doctor · 自检中…\n", file=sys.stderr)
    checks = await run_checks(str(Path.cwd()))
    text, code = summarize(checks)
    print(text)
    return code


async def run_canary_cli() -> int:
    """端到端交付验收：逐条报告哪条交付链路通、哪条断。有断的返回 1（部署脚本据此拦住上线）。"""
    from src.gateway.canary import run_canary, summarize
    print("VortoCode canary · 端到端交付验收（假通道 + 脚本模型，不触网）…\n", file=sys.stderr)
    lanes = await run_canary()
    for ln in lanes:
        print(f"{ln.glyph} {ln.name:<12} {ln.detail}")
    ok, text = summarize(lanes)
    print(f"\n{'✅' if ok else '⛔'} {text}")
    return 0 if ok else 1


def run_roster_cli(action: str, arg: str, roster_file: str) -> int:
    """多助手名册：看名单 / 体检 / 出 env 模板。**从不打印凭证值**，只报键名与"有没有撞车"。"""
    from src.gateway.roster import (check_roster, env_template, load_roster, roster_path,
                                    summarize, systemd_hint)
    cwd = str(Path.cwd())

    if action == "template":
        print(env_template(arg or "dingtalk"), end="")
        return 0

    roster = load_roster(cwd, roster_file)
    if action == "list":
        if not roster.assistants and not roster.errors:
            print(f"名册还是空的（{roster_path(cwd, roster_file)}）。\n"
                  "样例见 examples/systemd/vortocode-assistant@.service.example 顶部的安装三步；"
                  "env 模板：vc roster template dingtalk")
            return 0
        for a in roster.assistants:
            print(f"· {a.name}  {a.channel} · {a.mode} · {a.role}")
            print("  " + systemd_hint(a).replace("\n", "\n  "))
        for e in roster.errors:
            print(f"⛔ {e}")
        return 1 if roster.errors else 0

    findings = check_roster(roster)
    if not findings:
        print(f"名册是空的（{roster_path(cwd, roster_file)}）——没什么可查的。")
        return 0
    for f in findings:
        print(f"{f.glyph} {f.who:<16} {f.detail}")
    ok, text = summarize(findings)
    print(f"\n{'✅' if ok else '⛔'} {text}")
    return 0 if ok else 1


async def run_im(channel: str, *, mode: str = "plan"):
    """IM 通道桥入口：常驻长轮询，把主 agent 搬上 IM。fail-closed：缺配对凭证直接拒启。"""
    cwd = str(Path.cwd())
    from src.gateway.im_service import IMConfigError, build_adapter, load_allow_from
    try:
        adapter, owner = build_adapter(channel)      # 凭证解析/fail-closed 与 serve 内嵌同源，不抄两份
    except IMConfigError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(2)
    from src.im.bridge import IMBridge
    bridge = IMBridge(cwd, adapter, owner, channel=channel, mode=mode,   # standalone：自己的 runner
                      allow_from=load_allow_from(channel))
    who = "、".join(sorted(bridge.allow_from)) if bridge.allow_from else "空 → 拒绝一切入站"
    print(f"🌉 {channel} 桥启动（仓库 {Path(cwd).name}，{mode} 模式）。入站白名单：{who}；"
          f"群聊须显式 @ 本机器人。Ctrl-C 退出。", file=sys.stderr)
    warn = bridge.allow_from_warning()      # 白名单配错（空 / 漏了 owner）→ 开机就在终端讲清楚：
    if warn:                                # 不是坏了，是配置把你自己也挡在门外了
        print(warn, file=sys.stderr)
    try:
        await bridge.run()
    finally:
        await adapter.close()


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
    if raw == "-" or (raw is None and not _isatty(sys.stdin)):
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
    played = _isatty(sys.stdout) and _play_audio(out_path)
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


def _build_headless_agent(cwd, *, max_steps, on_tool, on_plan, confirm, llm=None, on_progress=None,
                          capability_profile="local", auto_approve=False, can_ask_human=False,
                          on_decision=None):
    """薄壳：装配走 gateway 的单一工厂（kind="cli"，无制品、读 .vortocode/hooks.yaml）。

    工具集与网页 /agent 同源（同一工厂），签名保持兼容供既有调用方/测试。
    on_progress：dev 流水线进度回调（长任务边跑边播到 stderr，免得对着静默 prompt 干等）。
    auto_approve / can_ask_human：交给内核的 confirm gate 判定（见 make_confirm_gate）——
    端只说"问不问得到人 / 有没有被授权自动放行"，不自己决定要不要问。
    """
    from src.gateway.agent_session import build_session
    return build_session(cwd, kind="cli", confirm=confirm, on_progress=on_progress,
                         on_tool=on_tool, on_plan=on_plan, llm=llm, max_steps=max_steps,
                         capability_profile=capability_profile,
                         auto_approve=auto_approve, can_ask_human=can_ask_human,
                         on_decision=on_decision)


def _looks_like_serve_url(s: str) -> bool:
    """s 像 --attach 的 URL 吗（scheme:// 或 host:port）——不像就是被 nargs="?" 误吞的任务文本。"""
    import re
    s = (s or "").strip()
    if s.startswith(("http://", "https://", "ws://", "wss://")):
        return True
    return re.fullmatch(r"[A-Za-z0-9.\-]+:\d{1,5}", s) is not None


def _disambiguate_attach(prompt, attach):
    """修 argparse `--attach [URL]` 吞任务文本：`vc agent --attach "修个 bug"` 里
    "修个 bug" 会被当成 URL、prompt 落空。若 prompt 空且 attach 值不像 URL，
    则它其实是任务文本——换回去（attach 用默认 URL）。"""
    if attach and prompt is None and not _looks_like_serve_url(attach):
        return attach, ""
    return prompt, attach


class ServeUnreachable(Exception):
    """attach 连接阶段失败（serve 不在/拒连/握手超时）——回合尚未发出，可安全回退进程内。"""


def _isatty(stream) -> bool:
    """这个流是不是 TTY——**必须防 None**。

    从 launchd / systemd / cron 起的进程，关掉的标准流会被 CPython 设成 **None**（不是文件对象），
    直接 `.isatty()` 就是 AttributeError。而 attach 的接收循环用兜底 except 把异常吞成"拒绝"——
    结果是无人值守的 `--yes` **静默拒掉每一次确认**、什么也没干、也不说为什么（自审实机复现）。
    拿不准一律当"不是 TTY"。
    """
    try:
        return bool(stream is not None and stream.isatty())
    except Exception:  # noqa: BLE001 —— 关闭的/异常的流 → 当作非 TTY
        return False


def _tty_can_ask_human() -> bool:
    """本进程问得到真人吗——需要 stdin 与 stderr 都是 TTY（拿不准 → 问不到，fail-closed）。"""
    return _isatty(sys.stdin) and _isatty(sys.stderr)


def _make_attach_confirm(auto_yes, say):
    """造 attach 端的确认门。**模块级**（不是闭包）是为了让三端契约表能直接驱动它——
    这一端此前手搓判定、又够不到测试，正是漂移最容易发生的角落。

    attach 是**跨进程**的：agent 在 serve 侧跑，污点状态（contextvar）传不过来。所以污点由协议的
    `agent_confirm.tainted` **结构化字段**下发（见 protocol.py）——绝不能靠匹配警示文案来猜：
    serve 换个版本/改个措辞，猜测就失效，`--yes` 又会在污点回合放行（fail-open）。老 serve 不发
    这个字段时，client 按 `tainted=True` 兜底（fail-closed）并置 `taint_known=False`。
    """
    async def confirm(text, *, tainted: bool = True, taint_known: bool = True):
        # 取**模块属性** `gate.decide`（而非 `from ... import decide`）：这样无论导入写在哪，
        # 契约测试 monkeypatch 内核判定都能生效——测试不该被导入位置绑架。
        from src.agents import gate

        first = (text or "").splitlines()[0] if text else ""
        # **判定不在这里做**：跨进程也好、手搓也罢，排序都必须来自内核那一处（gate.decide）。
        # attach 只负责如实申报三个输入 + 执行结果。此前这里自己写了一遍
        # `if auto_yes and not tainted`，于是内核新加的规矩这一端收不到（审核记为漂移风险）。
        verdict = gate.decide(tainted=tainted, pre_authorized=auto_yes,
                              can_ask_human=_tty_can_ask_human())
        if verdict == gate.ALLOW:
            say(f"✓ 自动确认：{first}")
            return True
        if verdict == gate.ASK:                          # 交互终端：真问人（attach 有人守着）
            print(f"需要确认：{text}", file=sys.stderr, flush=True)
            ans = (await asyncio.to_thread(input, "允许吗？[y/N] ")).strip().lower()
            return ans in ("y", "yes")
        if not taint_known:
            # tainted 是"未知→兜底为真"，不是"确知有污点"：对端 serve 版本旧、报不了本回合污点状态。
            # 别谎称"摄入过外部内容"，也别静默拒绝——给出可执行出路。
            why = "对端未上报本回合污点状态（serve 可能较旧）；--yes 从严不放行，请升级 serve 或用交互终端确认"
        elif tainted:
            why = gate.TAINT_REFUSED_REASON              # 单一真相源（headless 那头用的是同一条）
        else:
            why = "非交互终端，需 --yes 放行"
        say(f"✗ 拒绝（{why}）：{first}")
        return False

    return confirm


async def _run_agent_attached(prompt, url, *, build=False, auto_yes=False, as_json=False,
                              quiet=False, continue_session=False, images=None, audio=None,
                              speak=False, voice=None, speak_out=None, llm=None):
    """attach 模式：回合在常驻 serve 里跑，本进程只是协议客户端（渲染 + confirm 应答）。

    输出契约与进程内一致：emit → stdout（TTY 非 json 时按 stream 增量流式）、say → stderr、
    confirm → TTY 问询（--yes 自动允许；非 TTY 默认拒绝，安全优先）。退出码：done=0 /
    error=1 / cancelled=130。连接阶段失败抛 ServeUnreachable（调用方回退进程内）。
    """
    from src.gateway import protocol as gp
    from src.gateway.client import ProtocolClient, delete_session, local_ref_to_data_url
    from src.web.auth import get_api_token

    mode = "build" if build else "plan"
    # 语义对齐进程内（每轮都存、-c 才续）：attach 固定用 serve 侧 "cli" 会话；
    # 无 -c = 全新开始 → 先删掉旧会话（best-effort），有 -c 直接续上。
    sid = "cli"
    token = get_api_token() or None
    if not continue_session:
        await delete_session(url, sid, token=token)
    imgs = [local_ref_to_data_url(i) for i in (images or [])]
    auds = [local_ref_to_data_url(a, is_audio=True) for a in (audio or [])]

    def say(text):
        if not quiet:
            print(f"\033[2m{text}\033[0m", file=sys.stderr, flush=True)

    streaming = (not as_json) and _isatty(sys.stdout)
    seen = {"n": 0}                                # agent_stream 是累计文本，按长度算增量

    def on_stream(text):
        delta = text[seen["n"]:]
        if delta:
            sys.stdout.write(delta)
            sys.stdout.flush()
            seen["n"] = len(text)

    confirm = _make_attach_confirm(auto_yes, say)     # 判定走内核 gate.decide（见工厂 docstring）

    client = ProtocolClient(url, sid=sid, token=token)
    try:
        await client.__aenter__()
    except Exception as e:  # noqa: BLE001 —— 回合未发出，安全回退
        raise ServeUnreachable(str(e)[:200]) from e
    try:
        if client.server_version not in (None, gp.PROTOCOL_VERSION):
            say(f"⚠ serve 协议版本 v{client.server_version} ≠ 本客户端 v{gp.PROTOCOL_VERSION}，"
                f"事件面可能不齐")
        turn = asyncio.ensure_future(client.run_turn(
            prompt, mode=mode, images=imgs, audio=auds,
            on_say=say, on_stream=(on_stream if streaming else None), confirm=confirm))
        try:
            outcome = await asyncio.shield(turn)
        except asyncio.CancelledError:          # Ctrl-C：礼貌发 agent_cancel、等收尾再退
            await client.cancel()
            import contextlib as _ctx
            with _ctx.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(turn, 5)
            sys.exit(130)
    finally:
        await client.__aexit__()

    if as_json:
        import json as _json
        print(_json.dumps({"reply": outcome.reply, "mode": mode, "status": outcome.status,
                           "attached": True}, ensure_ascii=False, indent=2))
    elif streaming:
        if seen["n"] == 0 and outcome.reply:
            print(outcome.reply)
        elif seen["n"] and not outcome.reply.endswith("\n"):
            print()
    elif outcome.reply:
        print(outcome.reply)
    if outcome.status == "error":
        print(f"回合出错：{outcome.error}", file=sys.stderr, flush=True)
        sys.exit(1)
    if outcome.status == "cancelled":
        sys.exit(130)
    if speak and outcome.reply:                    # 语音回复在本地合成（与进程内同一路径）
        await _speak_reply(outcome.reply, voice, speak_out, llm, quiet)
    return outcome.reply


async def run_agent_headless(prompt, *, build=False, auto_yes=False, max_steps=None,
                             as_json=False, quiet=False, llm=None, images=None, audio=None,
                             speak=False, voice=None, speak_out=None, continue_session=False,
                             use_mcp=False, model=None, attach=None, capability_profile=None):
    """headless 跑一回合主 agent loop（仿 claude -p）：无 UI、跑完即返回。

    输出契约：最终回复 → stdout；工具调用/进度 → stderr（--quiet 静默）。
    TTY 且非 --json 时把回复流式写 stdout；管道/重定向/--json 则一次性输出（利于脚本/jq）。
    高危/外向工具（run_command/open_pr）默认拒绝，--yes 才放行（headless 无人值守，安全优先）。
    images: 可选图片引用列表（路径/URL/data URL），挂到本轮 user 消息（mimo-v2.5 能读图）。
    audio:  可选音频引用列表（路径/data URL），挂到本轮 user 消息（mimo-v2.5 能听音频）。
    speak:  True 则把最终回复合成成语音写 WAV（speak_out，默认 vorto-reply.wav）、TTY 下试播。
    continue_session: True 则续上上一次 CLI 对话（仿 claude -c）——把存盘历史灌回 agent；
                每轮结束都把历史落盘（.vortocode/cli_session.json），所以下次 -c 能接上。
    attach: 非 None 则走协议客户端模式（回合交给常驻 serve；"" = 默认 URL）。连接失败
                自动回退进程内（提示一行），**回合发出后的错误不回退**（防重复执行）。
    """
    import os
    from src.agents.capabilities import normalize_profile
    cwd = os.getcwd()
    mode = "build" if build else "plan"
    profile = normalize_profile(capability_profile or "local")

    if attach is not None:                        # --attach：先试 serve，够不着再进程内
        url = attach or os.getenv("VORTOCODE_SERVE_URL") or "http://127.0.0.1:8080"
        ignored = [n for n, v in (("--model", model), ("--max-steps", max_steps),
                                  ("--mcp", use_mcp), ("--capabilities", capability_profile)) if v]
        if ignored and not quiet:
            print(f"\033[2m⚠ attach 下由 serve 决定、本次忽略：{'、'.join(ignored)}\033[0m",
                  file=sys.stderr, flush=True)
        try:
            return await _run_agent_attached(
                prompt, url, build=build, auto_yes=auto_yes, as_json=as_json, quiet=quiet,
                continue_session=continue_session, images=images, audio=audio,
                speak=speak, voice=voice, speak_out=speak_out, llm=llm)
        except ServeUnreachable as e:
            if not quiet:
                print(f"\033[2m⚠ 未连上 serve（{url}：{e}），进程内执行\033[0m",
                      file=sys.stderr, flush=True)

    tools_log: list = []
    plan_holder = {"plan": []}

    def _on_tool(name, args, result):
        tools_log.append({"tool": name, "args": args})

    def _on_plan(plan):
        plan_holder["plan"] = plan

    # headless 没有交互终端 → 问不到人（can_ask_human=False）。--yes 是"已授权自动放行"。
    # **要不要问、能不能免，由内核的 confirm gate 判**（污点回合下 --yes 一律失效 → 拒绝）：
    # 让模型读了网页再自动放行写操作，正是提示注入最想要的路径。
    #
    # 不传 ask_human（传 None）：`can_ask_human=False` 时 gate 的 decide() 直接判 DENY、根本不会去问。
    # 此前这里塞了个"永远返回 False"的哨兵——**函数体永远执行不到**，正是本批次声称已删掉的
    # `deny_all` 维护陷阱换了个马甲又活了一遍（自审逮到）。fail-closed 只住在 gate 里，别处不设兜底。
    def _on_decision(operation, ok, tainted):
        """每个确认决定都过这里——自动放行也要留痕，自动拒绝要说清**拒的是什么**。

        注意用的是 `operation`（内核给的原始操作文案，不含污点警示横幅）：
        若直接取带横幅那份的首行，用户只会看到一大段警示、看不到被拒的究竟是哪条命令。
        """
        if quiet:
            return
        from src.agents.gate import TAINT_REFUSED_REASON

        first = (operation or "").splitlines()[0]
        if ok:
            print(f"\033[2m✓ 自动确认：{first}\033[0m", file=sys.stderr, flush=True)
        elif tainted:
            # 文案取自内核的单一真相源（attach 那头用的是同一条）——此前两处各写一份、已开始漂移。
            print(f"\033[2m✗ 拒绝（{TAINT_REFUSED_REASON}）：{first}\033[0m",
                  file=sys.stderr, flush=True)
        else:
            print(f"\033[2m✗ 自动拒绝（需 --yes 放行）：{first}\033[0m", file=sys.stderr, flush=True)

    def _progress(msg):                           # dev 流水线进度 → stderr（--quiet 静默）
        if not quiet:
            print(f"\033[2m{msg}\033[0m", file=sys.stderr, flush=True)

    agent = _build_headless_agent(cwd, max_steps=max_steps, on_tool=_on_tool,
                                  on_plan=_on_plan, confirm=None, llm=llm,
                                  on_progress=_progress, capability_profile=profile,
                                  auto_approve=auto_yes, can_ask_human=False,
                                  on_decision=_on_decision)
    if model:                                     # --model：本次覆盖 .env 的 DEFAULT_MODEL
        try:
            agent.set_model(model)
            if not quiet:
                print(f"\033[2m🧠 用模型 {model}\033[0m", file=sys.stderr, flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"设置模型失败: {e}", file=sys.stderr)
    if continue_session:                          # 续上一次 CLI 对话（claude -c 式）
        saved_profile = _load_cli_capability_profile(cwd)
        can_resume = saved_profile == profile
        hist = _load_cli_history(cwd) if can_resume else []
        if not can_resume and not quiet:
            print(f"\033[2m⚠ 上次 CLI 会话是 {saved_profile}，不能把历史带进 {profile} 信任域；已新开上下文\033[0m",
                  file=sys.stderr, flush=True)
        if hist:
            agent.history = hist
            if not quiet:
                print(f"\033[2m↩ 续上上次对话（{len(hist)} 条历史）\033[0m", file=sys.stderr, flush=True)

    mcp_mgr = None
    if use_mcp:                                   # --mcp：接 config/mcp.yaml 的 MCP 服务器工具
        from src.agents.mcp_tools import connect_mcp
        try:
            mcp_mgr, mcp_tools = await connect_mcp(cwd, capability_profile=profile)
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

    streaming = (not as_json) and _isatty(sys.stdout)
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
    _save_cli_history(cwd, getattr(agent, "history", []), profile)   # 落盘，供下次 --continue 接上
    if mcp_mgr is not None:                        # 关掉 MCP 子进程，别残留
        try:
            await mcp_mgr.shutdown()
        except Exception:  # noqa: BLE001
            pass
    return reply


_CLI_SESSION = ".vortocode/cli_session.json"


def _load_cli_session(cwd):
    """Read the persisted CLI session with backward-compatible defaults."""
    import json
    p = Path(cwd) / _CLI_SESSION
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_cli_history(cwd):
    """读回上次 CLI 对话历史（list）；不存在/坏文件 → []。"""
    data = _load_cli_session(cwd)
    return data.get("history", []) if isinstance(data.get("history"), list) else []


def _load_cli_capability_profile(cwd):
    value = _load_cli_session(cwd).get("capability_profile")
    if value is None:
        return None                                  # legacy files are treated as local-only by the caller
    from src.agents.capabilities import normalize_profile
    return normalize_profile(str(value))             # corrupt/unknown values fail closed to external


def _save_cli_history(cwd, history, capability_profile="local"):
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
        tmp.write_text(json.dumps({"history": out, "capability_profile": capability_profile},
                                  ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except (OSError, TypeError, ValueError):
        pass


async def _with_progress(coro, label: str = "运行中"):
    """跑 coro，运行期间在 stderr 单行刷新「⏳ label Ns…」，让人知道没卡死；结束即清行。

    长跑命令（开发流水线 / 自分析 / 自改进）此前打个标题就闷头跑，看不出是否在动。
    非 TTY（管道 / 重定向 / 日志）则完全静默、不污染输出。
    """
    import time as _time
    if not _isatty(sys.stderr):
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


def run_server(host: str, port: int, im: str = ""):
    """运行服务器（im 非空 = 内嵌 IM 桥，见 gateway/im_service）"""
    from src.web.server import start_server
    start_server(host, port, im=im)


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


def run_tui(attach=None):
    """启动交互式 TUI（仿 opencode）。未装 textual 时给出安装提示，不崩。

    attach 非 None = 协议客户端模式（"" 用缺省 URL：$VORTOCODE_SERVE_URL 或 127.0.0.1:8080）。
    """
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
    url = None
    if attach is not None:
        url = attach or os.getenv("VORTOCODE_SERVE_URL") or "http://127.0.0.1:8080"
    run_tui_app(attach=url)


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
