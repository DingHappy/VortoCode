#!/usr/bin/env python3
"""Auto-Dev-Crew CLI 实现（既是 console_scripts 入口 auto-dev-crew/adc，也被根 main.py 复用）。"""

import argparse
import asyncio
import sys
import textwrap
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Auto-Dev-Crew · 多 Agent 协作开发框架（能自分析 / 自改进自己）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            常用示例:
              %(prog)s self-analyze                       只读扫描自己、列出问题（无需 LLM key）
              %(prog)s run -t "实现阶乘函数及其单测"        跑完整开发流水线（需 key）
              %(prog)s self-improve --apply               给测试缺口自动补测试并写到新分支
              %(prog)s self-fix --paths src/foo.py        深审并外科修复指定文件
              %(prog)s server --port 8080                 启动 Web 控制台
              %(prog)s quant run --mock                   量化流水线（mock 数据）

            每个命令都有自己的帮助，例如:  %(prog)s self-fix -h
        """),
    )
    sub = parser.add_subparsers(dest="command", metavar="<命令>",
                                title="可用命令")

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

    if args.command == "run":
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


async def run_task(task: str):
    """运行任务"""
    from src.orchestrator import create_default_engine

    print(f"Running task: {task}")
    print("=" * 50)

    # 初始化引擎（注册全部 5 个角色 Agent：product/architect/developer/reviewer/tester）
    engine = await create_default_engine()

    # 执行任务
    result = await engine.orchestrate(task)
    
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
    analysis = await analyzer.analyze(task)
    
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
    report = await analyze_self(".", llm_paths=paths)
    print(render_report(report))


async def run_self_improvement(apply: bool = False, max_fixes: int = 3):
    """L2 自我改进：L1 找测试缺口 -> 生成测试 -> 真 pytest 门控 -> 提案（默认 dry-run）。

    需要配置 LLM（用于生成测试）。--apply 时把通过门控的测试写到一个新分支供 review。
    """
    from src.orchestrator.self_improve import SelfImprovementLoop, render_result

    loop = SelfImprovementLoop(".", max_fixes=max_fixes)
    result = await loop.propose()
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
    report = await analyze_self(".", llm_paths=paths)
    loop = CodeFixLoop(".", max_fixes=max_fixes)
    result = await loop.propose(report.findings)   # 内部只挑 bug/code-smell 类
    if apply and result.accepted:
        branch = loop.apply(result)
        print(f"已把 {len(result.accepted)} 个通过门控的修复写到分支: {branch}")
    print(render_result(result))


def run_tests():
    """运行测试"""
    import subprocess
    subprocess.run([sys.executable, "-m", "pytest", "tests/", "-v"])


def run_demo():
    """运行演示"""
    print("""
╔══════════════════════════════════════════════════════════════╗
║                 Auto-Dev-Crew 演示模式                        ║
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
