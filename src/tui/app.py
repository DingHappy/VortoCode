"""Auto-Dev-Crew 交互式全屏 TUI（仿 opencode 的形）。

一个 Textual 应用：上方对话区、中间实时流式区、下方输入区、底部状态栏。
- 自然语言 = 开发目标（等同 /run）；slash 命令驱动各能力。
- `@文件` 在输入时幽灵文本补全，运行时把文件内容带入上下文。
- token 级流式：开发过程（developer 写代码）边生成边显示。
- plan / build 模式（Tab）：plan=只读/只出提案；build=允许写新分支（绝不碰 main）。

需要 textual（`pip install '.[tui]'`）。无 LLM key 时，需要模型的命令优雅降级、不崩。
"""

from __future__ import annotations

import re
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.suggester import Suggester
from textual.widgets import Footer, Header, Input, RichLog, Static

HELP = """可用命令:
  直接输入自然语言   = 开发目标（等同 /run）；用 @文件 可补全并带入上下文
  /analyze            L1 自分析（只读扫描本仓库，无需 LLM key）
  /improve            L2 给测试缺口生成测试（需 key；build 模式下才写分支）
  /fix <文件,...>     L2.2 深审并外科修复指定文件（需 key；build 模式下才写分支）
  /run <目标>         跑开发循环（dev→test→review，流式）
  /mode               切换 plan(只读/提案) / build(可写分支)
  /clear              清屏
  /help               显示本帮助
  /quit               退出（也可 Ctrl+C）
键位: Tab=切模式  Ctrl+L=清屏  Ctrl+C=退出  输入 @ 触发文件补全（→ 接受）"""

_IGNORE = {"__pycache__", ".git", ".venv", "venv"}


def _repo_files(root: str) -> list[str]:
    """仓库内 .py 文件的相对路径，供 @ 补全。"""
    base = Path(root)
    out: list[str] = []
    for sub in ("src", "tests"):
        d = base / sub
        if d.is_dir():
            out += [
                str(p.relative_to(base))
                for p in sorted(d.rglob("*.py"))
                if not any(part in _IGNORE for part in p.parts)
            ]
    return out[:2000]


class FileSuggester(Suggester):
    """输入里出现 @前缀时，补全为匹配的仓库文件路径（幽灵文本）。"""

    def __init__(self, repo_root: str = "."):
        super().__init__(use_cache=True, case_sensitive=False)
        self._files = _repo_files(repo_root)

    async def get_suggestion(self, value: str) -> str | None:
        at = value.rfind("@")
        if at == -1:
            return None
        prefix = value[at + 1:]
        if not prefix or " " in prefix:    # 该 @token 已输完
            return None
        pl = prefix.lower()
        cand = (next((f for f in self._files if f.lower().startswith(pl)), None)
                or next((f for f in self._files if pl in f.lower()), None))
        return value[:at + 1] + cand if cand else None


class AutoDevCrewTUI(App):
    """仿 opencode 的交互式开发 TUI。"""

    TITLE = "Auto-Dev-Crew"
    CSS = """
    #log { height: 1fr; border: round $accent; padding: 0 1; }
    #stream { max-height: 10; overflow-y: auto; color: $text-muted; padding: 0 1; }
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
        yield Static(id="stream")
        yield Input(placeholder="输入需求（自然语言），或 /help 看命令…",
                    id="prompt", suggester=FileSuggester(self.repo_root))
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#stream", Static).display = False
        self._chrome("[b]Auto-Dev-Crew[/b] 交互模式 · 直接说需求，或 [b]/help[/b] 看命令")
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
            self._do_run(text)          # 自然语言 = 开发目标

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

    # ---------------------------------------------------------------- @文件
    def _expand_at_files(self, text: str) -> tuple[str, list[str]]:
        """把 @存在的文件 token 去掉 @（留路径），并收集这些文件；不存在的原样保留。"""
        files: list[str] = []

        def repl(m: re.Match) -> str:
            rel = m.group(1)
            if (Path(self.repo_root) / rel).is_file():
                files.append(rel)
                return rel
            return m.group(0)

        return re.sub(r"@(\S+)", repl, text), files

    def _read_files(self, rels: list[str]) -> str:
        chunks = []
        for rel in rels:
            try:
                content = (Path(self.repo_root) / rel).read_text(encoding="utf-8")[:3000]
                chunks.append(f"# {rel}\n{content}")
            except OSError:
                continue
        return "\n\n".join(chunks)

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
            rel = [p.strip() for p in paths.replace("@", "").split(",") if p.strip()]
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
        goal_text, ctx_files = self._expand_at_files(goal)
        if ctx_files:
            self._chrome(f"[dim]带入文件上下文: {', '.join(ctx_files)}[/dim]")
        self._chrome(f"[cyan]开发（dev→test→review，流式）：{goal_text}[/cyan]")

        stream = self.query_one("#stream", Static)
        stream.display = True
        buf: list[str] = []

        def on_token(tok: str) -> None:
            buf.append(tok)
            stream.update(Text("".join(buf)[-1500:]))   # 显示尾部，避免无限增高

        async def on_iter(record) -> None:
            ok = "✓" if record.tests_passed else "✗"
            self._emit(f"  第{record.iteration}轮 · 测试{ok} · 审查={record.review_verdict or '—'}")
            buf.clear()
            stream.update("")

        from src.orchestrator.dev_loop import IterativeDevLoop
        from src.agents.roles import DeveloperAgent, TesterAgent, ReviewerAgent
        try:
            task = goal_text
            if ctx_files:
                task += "\n\n相关文件:\n" + self._read_files(ctx_files)
            loop = IterativeDevLoop(DeveloperAgent(), TesterAgent(), ReviewerAgent(),
                                    max_iterations=2)
            result = await loop.run(task, on_token=on_token, on_iteration=on_iter)
            ok = "成功" if result.success else "未完成"
            self._emit(f"结果: {ok} · 迭代 {result.iterations} · 工作区 {result.workspace}")
            if result.files:
                self._emit("产出文件: " + ", ".join(result.files))
        except Exception as e:  # noqa: BLE001
            self._emit(f"执行出错: {e}")
        finally:
            stream.update("")
            stream.display = False


def run() -> None:
    """启动 TUI（供 CLI 调用）。"""
    AutoDevCrewTUI(repo_root=".").run()
