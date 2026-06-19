"""量化研究交互式 CLI

提供终端内的交互式操作界面：
- 实时状态面板
- 命令行操作（启动/停止/执行阶段/健康检查）
- 彩色输出
"""

import asyncio
import json
import sys
import time
from datetime import datetime
from typing import Any, Optional


# ---- ANSI 颜色 ----

class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    BG = "\033[48;5;235m"


def _clear_screen():
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def _move_cursor(row: int, col: int):
    sys.stdout.write(f"\033[{row};{col}H")
    sys.stdout.flush()


def _hide_cursor():
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()


def _show_cursor():
    sys.stdout.write("\033[?25h")
    sys.stdout.flush()


# ---- 仪表盘渲染 ----

def _render_dashboard(pipeline, logs: list):
    """渲染终端仪表盘"""
    _clear_screen()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"{C.BOLD}{C.CYAN}╔══════════════════════════════════════════════════════════════╗{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}║{C.RESET}  {C.BOLD}量化研究平台 - 交互式 CLI{C.RESET}                              {C.BOLD}{C.CYAN}║{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}╠══════════════════════════════════════════════════════════════╣{C.RESET}")

    # 状态
    status = pipeline.get_status()
    running_loops = sum(1 for s in status.values() if s["status"] == "running")
    total_iter = sum(s.get("iterations", 0) for s in status.values())
    status_color = C.GREEN if running_loops > 0 else C.YELLOW
    status_text = f"{running_loops} 个运行中" if running_loops > 0 else "已停止"

    print(f"{C.CYAN}║{C.RESET}  状态: {status_color}{C.BOLD}{status_text}{C.RESET}  |  "
          f"迭代: {C.WHITE}{total_iter}{C.RESET}  |  "
          f"{C.DIM}{now}{C.RESET}")
    print(f"{C.CYAN}╠══════════════════════════════════════════════════════════════╣{C.RESET}")

    # 各循环状态
    stage_names = {"quant_news": "新闻采集", "quant_sentiment": "情绪分析",
                   "quant_factor": "因子研究", "quant_review": "每日复盘"}

    for name, info in status.items():
        display = stage_names.get(name, name)
        s = info["status"]
        sc = C.GREEN if s == "running" else C.DIM
        iters = info.get("iterations", 0)
        interval = info.get("interval", 0)
        last = info.get("last_check", "--")
        if last and last != "--":
            try:
                last = datetime.fromisoformat(last).strftime("%H:%M:%S")
            except Exception:
                pass

        bar = f"{sc}{'━' * 8}{C.RESET}" if s == "running" else f"{C.DIM}{'─' * 8}{C.RESET}"
        print(f"{C.CYAN}║{C.RESET}  {display:8s}  {bar}  "
              f"迭代 {C.WHITE}{iters:>4}{C.RESET}  "
              f"间隔 {interval:>5}s  "
              f"上次 {C.DIM}{last}{C.RESET}")

    print(f"{C.CYAN}╠══════════════════════════════════════════════════════════════╣{C.RESET}")

    # 最近日志
    recent = logs[-8:]
    for entry in recent:
        t, msg, typ = entry
        tc = C.GREEN if typ == "ok" else C.RED if typ == "err" else C.DIM
        print(f"{C.CYAN}║{C.RESET}  {C.DIM}{t}{C.RESET}  {tc}{msg}{C.RESET}")

    # 填充空行
    for _ in range(max(0, 8 - len(recent))):
        print(f"{C.CYAN}║{C.RESET}")

    print(f"{C.CYAN}╠══════════════════════════════════════════════════════════════╣{C.RESET}")
    print(f"{C.CYAN}║{C.RESET}  {C.BOLD}命令:{C.RESET} "
          f"{C.GREEN}s{C.RESET}tart  "
          f"{C.RED}p{C.RESET}stop  "
          f"{C.YELLOW}n{C.RESET}ews  "
          f"{C.YELLOW}e{C.RESET}motion  "
          f"{C.YELLOW}f{C.RESET}actor  "
          f"{C.YELLOW}r{C.RESET}eview  "
          f"{C.BLUE}h{C.RESET}ealth  "
          f"{C.DIM}q{C.RESET}uit")
    print(f"{C.BOLD}{C.CYAN}╚══════════════════════════════════════════════════════════════╝{C.RESET}")


# ---- 交互式 CLI 主循环 ----

async def run_quant_cli(pipeline):
    """交互式 CLI 主循环"""
    import threading

    logs = []
    running = True
    dashboard_dirty = True

    def log(msg, typ=""):
        logs.append((datetime.now().strftime("%H:%M:%S"), msg, typ))
        nonlocal dashboard_dirty
        dashboard_dirty = True

    # 后台刷新线程
    def refresh_loop():
        nonlocal dashboard_dirty
        while running:
            if dashboard_dirty:
                _render_dashboard(pipeline, logs)
                dashboard_dirty = False
            time.sleep(1)

    _hide_cursor()
    t = threading.Thread(target=refresh_loop, daemon=True)
    t.start()

    log("量化研究 CLI 已启动，输入命令操作", "ok")

    try:
        while running:
            try:
                cmd = await asyncio.get_event_loop().run_in_executor(None, lambda: input("> "))
            except EOFError:
                break

            cmd = cmd.strip().lower()
            if not cmd:
                continue

            if cmd in ("q", "quit", "exit"):
                log("正在停止...")
                await pipeline.stop_all()
                running = False
                break

            elif cmd in ("s", "start"):
                log("正在启动流水线...")
                try:
                    await pipeline.start_all()
                    log("流水线已启动", "ok")
                except Exception as e:
                    log(f"启动失败: {e}", "err")

            elif cmd in ("p", "stop"):
                log("正在停止流水线...")
                await pipeline.stop_all()
                log("流水线已停止", "ok")

            elif cmd in ("n", "news"):
                log("执行: 新闻采集...")
                try:
                    result = await pipeline._run_news_collection()
                    ok = getattr(result, "success", False)
                    log(f"新闻采集: {'成功' if ok else '失败'}", "ok" if ok else "err")
                except Exception as e:
                    log(f"新闻采集失败: {e}", "err")

            elif cmd in ("e", "emotion", "sentiment"):
                log("执行: 情绪分析...")
                try:
                    result = await pipeline._run_sentiment_analysis()
                    ok = getattr(result, "success", False)
                    log(f"情绪分析: {'成功' if ok else '失败'}", "ok" if ok else "err")
                except Exception as e:
                    log(f"情绪分析失败: {e}", "err")

            elif cmd in ("f", "factor"):
                log("执行: 因子研究...")
                try:
                    result = await pipeline._run_factor_research()
                    ok = getattr(result, "success", False)
                    log(f"因子研究: {'成功' if ok else '失败'}", "ok" if ok else "err")
                except Exception as e:
                    log(f"因子研究失败: {e}", "err")

            elif cmd in ("r", "review"):
                log("执行: 每日复盘...")
                try:
                    result = await pipeline._run_daily_review()
                    ok = getattr(result, "success", False)
                    log(f"每日复盘: {'成功' if ok else '失败'}", "ok" if ok else "err")
                except Exception as e:
                    log(f"每日复盘失败: {e}", "err")

            elif cmd in ("h", "health"):
                log("执行健康检查...")
                try:
                    from src.tools.quant_mcp_server import handle_tool_call
                    result = await handle_tool_call("quant_system_info", {})
                    data = json.loads(result) if isinstance(result, str) else result
                    if "error" not in data:
                        log("quant-platform: 可达", "ok")
                    else:
                        log(f"quant-platform: {data['error']}", "err")
                except Exception as e:
                    log(f"quant-platform: 不可达 ({e})", "err")

            elif cmd in ("st", "status"):
                dashboard_dirty = True

            else:
                log(f"未知命令: {cmd}，输入 q 退出", "err")

    finally:
        _show_cursor()
        _clear_screen()
        print(f"{C.GREEN}已退出量化 CLI。{C.RESET}")
