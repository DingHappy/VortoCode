#!/usr/bin/env python3
"""Auto-Dev-Crew 主入口"""

import argparse
import asyncio
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Auto-Dev-Crew - 多 Agent 协作开发平台")
    parser.add_argument(
        "command",
        choices=["run", "server", "analyze", "self-analyze", "self-improve", "self-fix", "test", "demo", "quant"],
        help="Command to execute"
    )
    parser.add_argument("--task", "-t", help="Task to execute")
    parser.add_argument("--paths", help="逗号分隔的文件，对其跑 LLM 深审（self-analyze 用，可选）")
    parser.add_argument("--apply", action="store_true", help="self-improve: 把通过门控的测试写到新分支（默认 dry-run）")
    parser.add_argument("--max-fixes", type=int, default=3, help="self-improve: 单次最多处理几个问题")
    parser.add_argument("--host", default="127.0.0.1", help="Server host")
    parser.add_argument("--port", type=int, default=8080, help="Server port")
    parser.add_argument("--interval", type=int, default=0, help="Quant pipeline interval override (seconds)")
    parser.add_argument("--stage", choices=["news", "sentiment", "factor", "review", "all"], default="all", help="Quant pipeline stage to run")
    parser.add_argument("--server", action="store_true", help="Start web server with quant API (for quant command)")
    parser.add_argument("--cli", action="store_true", help="Start interactive CLI dashboard (for quant command)")
    parser.add_argument("--check", action="store_true", help="Check environment readiness (for quant command)")
    parser.add_argument("--results", action="store_true", help="View recent quant results from memory (for quant command)")
    parser.add_argument("--mock", action="store_true", help="Use mock MCP server for testing without quant-platform")
    
    args = parser.parse_args()

    # 结构化日志（opt-in）：AUTODEV_JSON_LOGS=1 控制台 JSON；AUTODEV_LOG_FILE=路径 落盘（ELK-ready）
    import os as _os
    if _os.getenv("AUTODEV_JSON_LOGS") or _os.getenv("AUTODEV_LOG_FILE"):
        from src.core.tracing import setup_structured_logging
        setup_structured_logging(log_file=_os.getenv("AUTODEV_LOG_FILE") or None)

    if args.command == "run":
        if not args.task:
            print("Error: --task is required for 'run' command")
            sys.exit(1)
        asyncio.run(run_task(args.task))
    
    elif args.command == "server":
        run_server(args.host, args.port)
    
    elif args.command == "analyze":
        if not args.task:
            print("Error: --task is required for 'analyze' command")
            sys.exit(1)
        asyncio.run(analyze_task(args.task))
    
    elif args.command == "self-analyze":
        paths = [p.strip() for p in args.paths.split(",")] if args.paths else None
        asyncio.run(run_self_analysis(paths))

    elif args.command == "self-improve":
        asyncio.run(run_self_improvement(apply=args.apply, max_fixes=args.max_fixes))

    elif args.command == "self-fix":
        paths = [p.strip() for p in args.paths.split(",")] if args.paths else None
        asyncio.run(run_self_fix(paths, apply=args.apply, max_fixes=args.max_fixes))

    elif args.command == "test":
        run_tests()
    
    elif args.command == "demo":
        run_demo()

    elif args.command == "quant":
        if args.check:
            from src.orchestrator.quant_check import check_environment
            asyncio.run(check_environment())
        elif args.results:
            asyncio.run(show_quant_results())
        elif args.server:
            run_server(args.host, args.port)
        elif args.cli:
            asyncio.run(run_quant_cli())
        else:
            asyncio.run(run_quant(args.stage, args.interval, mock=args.mock))


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
