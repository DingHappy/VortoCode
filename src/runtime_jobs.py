"""Internal scheduled-job adapters. No scheduler, new permissions or public CLI imports."""

import argparse
import asyncio
import sys
from pathlib import Path

from src.utils.state_dir import state_dir

JOB_COMMANDS = frozenset({"cron", "collect", "stats", "digest", "pipeline", "heartbeat"})


def add_job_commands(commands):
    p = commands.add_parser(
        "cron", help="定时作业（.vortoone/cron.yaml）：list 看表 / run <name> 手动触发一次"
    )
    p.add_argument("action", choices=["list", "run"], help="list 列出作业 / run 手动跑一个")
    p.add_argument("name", nargs="?", help="run 时的作业名")

    p = commands.add_parser(
        "collect",
        help="信号采集：抓 HN/GitHub/RSS/V2EX，落一条 signals 产出物"
        "（确定性、零 LLM，给 cron 的 command: 档用）",
    )
    p.add_argument("-q", "--quiet", action="store_true", help="只报结果，不列条目")

    p = commands.add_parser(
        "stats", help="渠道数据查询：把已登记的发布链接查一遍，落一条 channel_stats"
    )
    p.add_argument("-q", "--quiet", action="store_true", help="只报结果，不列条目")

    p = commands.add_parser(
        "digest", help="把最新一份 signals 渲染成早报（零 LLM，给 cron 的 command: 档用）"
    )
    p.add_argument("-n", "--limit", type=int, default=12, help="最多列几条（默认 12）")

    p = commands.add_parser(
        "pipeline", help="作业流水线（.vortoone/pipelines/*.yaml）：非代码流程的工序 + 人批闸门"
    )
    p.add_argument(
        "action",
        choices=["list", "show", "start", "advance", "review", "tick"],
        help="list 看全部 / show <run> 看细节 / start <名字> 开一轮 / "
        "advance <run> 推一道工序 / review <run> 给人批回执 / "
        "tick 无人值守扫一遍（给 cron 用，状态有变化才通报）",
    )
    p.add_argument(
        "name",
        nargs="?",
        default="",
        help="run id（show/advance/review）、流水线名（start，或 tick 时只扫这一条）",
    )
    p.add_argument(
        "--approve",
        dest="verdict",
        action="store_const",
        const="approve",
        help="review：放行，下次 advance 继续",
    )
    p.add_argument(
        "--reject",
        dest="verdict",
        action="store_const",
        const="reject",
        help="review：驳回重做（意见落成产出物进血缘，旧版不删）",
    )
    p.add_argument(
        "--defer",
        dest="verdict",
        action="store_const",
        const="defer",
        help="review：挂起——不推进也不失败",
    )
    p.add_argument("-m", "--comment", default="", help="人批意见（驳回时会进血缘）")
    p.add_argument("--review-token", default="", help="show 显示的产出版本凭据，review 时必须提供")
    p.add_argument(
        "--rollback-to",
        default="",
        help="驳回时退回哪道工序（默认退回当前这道）；退回点由你指定，不让模型猜",
    )
    p.add_argument(
        "--max-stages",
        type=int,
        default=1,
        help="一次最多推几道工序（默认 1——每道都是一次真 LLM 回合）",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="自动批准允许的类别；outbound 工序仍需主人确认，非交互终端拒绝外发",
    )
    p.add_argument(
        "--if-idle",
        action="store_true",
        help="start 时：已有未完成的运行就不开新的（定时开轮必备，防堆积）",
    )

    p = commands.add_parser(
        "heartbeat", help="心跳值班一次（读 .vortoone/HEARTBEAT.md + 领 BACKLOG.md）"
    )
    p.add_argument("action", choices=["run"], help="run：立刻值班一次（隔离会话、便宜模型）")


def validate_job_args(parser: argparse.ArgumentParser, args: argparse.Namespace):
    if args.command == "cron" and args.action == "run" and not args.name:
        parser.error("cron run requires a job name")
    if (
        args.command == "pipeline"
        and args.action in {"start", "show", "advance", "review"}
        and not args.name
    ):
        parser.error("pipeline action requires a name")


def run_job_command(args):
    if args.command == "cron":
        asyncio.run(run_cron(args.action, args.name))
        return 0
    if args.command == "heartbeat":
        asyncio.run(run_heartbeat_cli())
        return 0
    if args.command in {"collect", "stats", "digest"}:
        from src.workflow.pipeline.collect_cli import run_collect_cli, run_stats_cli, run_digest_cli

        if args.command == "collect":
            return run_collect_cli(quiet=args.quiet)
        if args.command == "stats":
            return run_stats_cli(quiet=args.quiet)
        return run_digest_cli(limit=args.limit)
    if args.command == "pipeline":
        from src.workflow.pipeline.pipeline_cli import run_pipeline_cli

        return asyncio.run(
            run_pipeline_cli(
                args.action,
                args.name,
                verdict=args.verdict or "",
                comment=args.comment,
                rollback_to=args.rollback_to,
                max_stages=args.max_stages,
                yes=args.yes,
                if_idle=args.if_idle,
                review_token=args.review_token,
            )
        )
    raise ValueError("unsupported internal job command")


async def run_cron(action: str, name=None):
    """cron 子命令：list 列出作业 / run <name> 手动触发一次（隔离会话真跑，进度到 stderr）。"""
    from src.scheduling import cron as _cron

    cwd = str(Path.cwd())
    if action == "list":
        jobs = _cron.load_jobs(cwd)
        if not jobs:
            print("（无 cron 作业；在 .vortoone/cron.yaml 里定义 jobs）")
            return
        state = _cron.CronState(cwd)
        for j in jobs:
            last = state.last_run(j.name)
            flag = "" if j.enabled else "（禁用）"
            print(
                f"· {j.name}{flag}  [{j.schedule.raw}]  announce={j.announce}  "
                f"上次={last.isoformat(timespec='minutes') if last else '从未'}"
            )
        return
    # action == "run"
    if not name:
        print("用法：vortoone-runtime cron run <name>", file=sys.stderr)
        sys.exit(2)
    if not any(j.name == name for j in _cron.load_jobs(cwd)):
        print(f"✗ 找不到 cron 作业 {name}（vortoone-runtime cron list 看有哪些）", file=sys.stderr)
        sys.exit(2)
    print(f"⏰ 手动触发 cron [{name}]（隔离会话）…", file=sys.stderr)
    result = await _cron.run_job_by_name(cwd, name)
    print((result or "（无输出）").strip())


def make_background_development_worker(root):
    from src.agents.main_agent import build_dev_tools
    from src.agents.dev_plan import load_plan
    from src.workflow.tasks.development_worker import execute_development

    async def worker(task, on_progress):
        return await execute_development(
            root, task, on_progress, build_tools=build_dev_tools, load_plan=load_plan
        )

    return worker


async def run_heartbeat_cli(*, worker_factory=None):
    """heartbeat run：立刻值班一次（隔离会话、便宜模型）。领 backlog 时 submit 并**等它真跑完**。

    这是一次性命令：若领了 backlog 活就 submit 到本地 runner，然后 **drain（await）** 那个任务——
    否则 asyncio.run 退出时 pending 任务会被取消，backlog 却已被标 [~]（领走了没干完，误导，#129 评审）。
    """
    from src.scheduling import heartbeat as _hb
    from src.gateway.runtime import RuntimeServices
    from src.gateway.process_lease import ProcessLease
    from src.gateway.workspace_scope import current_workspace_scope

    cwd = str(Path.cwd())
    if current_workspace_scope() != "project":
        raise RuntimeError("Autonomous scheduling requires a Project workspace")
    with ProcessLease(state_dir(cwd) / "runtime.lock"):
        worker = (worker_factory or make_background_development_worker)(cwd)
        runner = RuntimeServices(cwd, development_worker=worker).runner
        try:
            submitted: list = []

            async def _submit(item):
                t = await runner.submit(item, kind="dev")
                submitted.append(t.id)  # 记下来，函数返回前 drain，别让进程退出把它取消

            async def _notify(text):
                print(text)

            print("🫀 心跳值班一次…", file=sys.stderr)
            res = await _hb.run_heartbeat(cwd, submit=_submit, notify=_notify)
            for tid in submitted:  # 等领到的活真正跑完（否则 backlog 标了 [~] 却没干完）
                print(f"⏳ 等后台任务 {tid} 跑完…", file=sys.stderr)
                await runner.join(tid)
                done = runner.get(tid)
                print(f"[task {tid}] {done.status if done else '?'}", file=sys.stderr)
            print(f"[heartbeat] action={res['action']}", file=sys.stderr)
        finally:
            await runner.shutdown()
