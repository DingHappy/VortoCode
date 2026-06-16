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
        choices=["run", "server", "analyze", "test", "demo"],
        help="Command to execute"
    )
    parser.add_argument("--task", "-t", help="Task to execute")
    parser.add_argument("--host", default="127.0.0.1", help="Server host")
    parser.add_argument("--port", type=int, default=8080, help="Server port")
    
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
    
    elif args.command == "test":
        run_tests()
    
    elif args.command == "demo":
        run_demo()


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


if __name__ == "__main__":
    main()
