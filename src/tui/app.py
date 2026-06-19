"""Auto-Dev-Crew 交互式全屏 TUI（仿 opencode 的形）。

一个 Textual 应用：上方对话区、下方输入区、底部状态栏。
- 自然语言 + slash 命令驱动；动作在 worker 里跑，不阻塞 UI。
- plan / build 两种模式（Tab 切换）：plan=只读/只出提案；build=允许写新分支（绝不碰 main）。
- 把已有的 L1(self-analyze) / L2(self-improve) / L2.2(self-fix) / 开发流水线 串到这个前端后面。

需要 textual（`pip install '.[tui]'`）。无 LLM key 时，需要模型的命令会优雅降级、不崩。
"""

from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, RichLog

HELP = """可用命令:
  /analyze            L1 自分析（只读扫描本仓库，无需 LLM key）
  /improve            L2 给测试缺口生成测试（需 key；build 模式下才写分支）
  /fix <文件,...>     L2.2 深审并外科修复指定文件（需 key；build 模式下才写分支）
  /run <目标>         跑完整开发流水线（需 key）
  /mode               切换 plan(只读/提案) / build(可写分支)
  /clear              清屏
  /help               显示本帮助
  /quit               退出（也可 Ctrl+C）
键位: Tab=切模式  Ctrl+L=清屏  Ctrl+C=退出"""


class AutoDevCrewTUI(App):
    """仿 opencode 的交互式开发 TUI。"""

    TITLE = "Auto-Dev-Crew"
    CSS = """
    #log { height: 1fr; border: round $accent; padding: 0 1; }
    #prompt { border: round $panel; }
    """
    BINDINGS = [
        Binding("ctrl+c", "quit", "退出", priority=True),
        Binding("tab", "toggle_mode", "切模式"),
        Binding("ctrl+l", "clear_log", "清屏"),
    ]

    def __init__(self, repo_root: str = "."):
        super().__init__()
        self.repo_root = repo_root
        self.mode = "plan"                  # plan | build
        self.transcript: list[str] = []     # 完整记录，便于回看与测试

    # ---------------------------------------------------------------- 布局
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="log", wrap=True, markup=True, highlight=False, auto_scroll=True)
        yield Input(placeholder="输入需求，或 /help 看命令…", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self._chrome("[b]Auto-Dev-Crew[/b] 交互模式 · 输入 [b]/help[/b] 看命令")
        self._sync_subtitle()
        self.query_one("#prompt", Input).focus()

    # ---------------------------------------------------------------- 输出
    def _chrome(self, markup: str) -> None:
        """UI 提示（解析 Rich 标记）。"""
        self.transcript.append(markup)
        self.query_one("#log", RichLog).write(markup)

    def _emit(self, text: str) -> None:
        """工具输出（按字面写，避免 [xxx] 被当成标记解析）。"""
        self.transcript.append(text)
        self.query_one("#log", RichLog).write(Text(text))

    def _sync_subtitle(self) -> None:
        desc = "只读/提案" if self.mode == "plan" else "可写分支"
        self.sub_title = f"模式 {self.mode}（{desc}）"

    # ---------------------------------------------------------------- 键位动作
    def action_toggle_mode(self) -> None:
        self.mode = "build" if self.mode == "plan" else "plan"
        self._sync_subtitle()
        self._chrome(f"→ 切到 [b]{self.mode}[/b] 模式")

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()

    # ---------------------------------------------------------------- 输入分发
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.query_one("#prompt", Input).value = ""
        if not text:
            return
        self._chrome(f"[dim]› {text}[/dim]")
        if text.startswith("/"):
            self._dispatch(text)
        else:
            self._chrome("提示: v1 用 slash 命令驱动，如 [b]/run 目标[/b] 或 [b]/analyze[/b]。/help 看全部。")

    def _dispatch(self, text: str) -> None:
        parts = text[1:].split(maxsplit=1)
        cmd = parts[0] if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd in ("help", "h"):
            self._emit(HELP)
        elif cmd in ("quit", "exit", "q"):
            self.exit()
        elif cmd == "mode":
            self.action_toggle_mode()
        elif cmd == "clear":
            self.action_clear_log()
        elif cmd == "analyze":
            self._do_analyze()
        elif cmd == "improve":
            self._do_improve()
        elif cmd == "fix":
            if arg:
                self._do_fix(arg)
            else:
                self._chrome("[red]/fix 需要文件参数[/red]，如 /fix src/foo.py")
        elif cmd == "run":
            if arg:
                self._do_run(arg)
            else:
                self._chrome("[red]/run 需要目标[/red]，如 /run 实现一个阶乘函数")
        else:
            self._chrome(f"[red]未知命令 /{cmd}[/red] · /help 看命令")

    # ---------------------------------------------------------------- 动作（worker，不阻塞 UI）
    @work(exclusive=True, group="action")
    async def _do_analyze(self) -> None:
        self._chrome("[cyan]运行 L1 自分析…[/cyan]")
        from src.orchestrator.self_analysis import analyze_self, render_report
        try:
            report = await analyze_self(self.repo_root)
            self._emit(render_report(report))
        except Exception as e:  # noqa: BLE001
            self._emit(f"分析出错: {e}")

    @work(exclusive=True, group="action")
    async def _do_improve(self) -> None:
        self._chrome("[cyan]L2 自改进：测试缺口 → 生成测试 → 真 pytest 门控…[/cyan]")
        from src.orchestrator.self_improve import SelfImprovementLoop, render_result
        try:
            loop = SelfImprovementLoop(self.repo_root)
            result = await loop.propose()
            if self.mode == "build" and result.accepted:
                branch = loop.apply(result)
                self._chrome(f"[green]已写入分支 {branch}（请 review 后合并）[/green]")
            self._emit(render_result(result))
        except Exception as e:  # noqa: BLE001
            self._emit(f"自改进出错: {e}")

    @work(exclusive=True, group="action")
    async def _do_fix(self, paths: str) -> None:
        self._chrome("[cyan]L2.2 代码修复：深审 → 外科修改 → 全量门控…[/cyan]")
        from src.orchestrator.self_analysis import analyze_self
        from src.orchestrator.code_fix import CodeFixLoop, render_result
        try:
            rel = [p.strip() for p in paths.split(",") if p.strip()]
            report = await analyze_self(self.repo_root, llm_paths=rel)
            loop = CodeFixLoop(self.repo_root)
            result = await loop.propose(report.findings)
            if self.mode == "build" and result.accepted:
                branch = loop.apply(result)
                self._chrome(f"[green]已写入分支 {branch}（请 review 后合并）[/green]")
            self._emit(render_result(result))
        except Exception as e:  # noqa: BLE001
            self._emit(f"修复出错: {e}")

    @work(exclusive=True, group="action")
    async def _do_run(self, goal: str) -> None:
        self._chrome(f"[cyan]跑完整开发流水线：{goal}[/cyan]")
        from src.orchestrator import create_default_engine
        try:
            engine = await create_default_engine()
            result = await engine.orchestrate(goal)
            ok = "成功" if result.success else "失败"
            self._emit(f"结果: {ok} · 子任务 {len(result.results)} · {result.duration:.1f}s")
            if result.error:
                self._emit(f"错误: {result.error}")
        except Exception as e:  # noqa: BLE001
            self._emit(f"执行出错: {e}")


def run() -> None:
    """启动 TUI（供 CLI 调用）。"""
    AutoDevCrewTUI(repo_root=".").run()
