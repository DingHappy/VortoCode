"""Auto-Dev-Crew 交互式全屏 TUI（仿 opencode 的形）。

一个 Textual 应用：上方对话区、中间实时流式区、下方输入区、底部状态栏。
- 自然语言 = 开发目标（等同 /run）；slash 命令驱动各能力。
- `@文件` 在输入时幽灵文本补全，运行时把文件内容带入上下文。
- token 级流式：开发过程（developer 写代码）边生成边显示。
- plan / build 模式（Tab）：plan=只读/只出提案；build=允许写新分支（绝不碰 main）。

需要 textual（`pip install '.[tui]'`）。无 LLM key 时，需要模型的命令优雅降级、不崩。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.suggester import Suggester
from textual.widgets import Footer, Header, Input, RichLog, Static
from textual.worker import WorkerState

from src.memory.session_store import SessionManager

SLASH_COMMANDS = [
    "/analyze", "/improve", "/fix", "/run", "/agents", "/runagent",
    "/sessions", "/resume", "/new", "/mode", "/clear", "/help", "/quit",
]
ACTION_CMDS = {"analyze", "improve", "fix", "run", "runagent"}   # 跑长任务，受忙碌态约束

_AGENTS_DB = lambda root: str(Path(root) / ".auto-dev-crew" / "web_advanced_agents.json")

HELP = """可用命令:
  直接输入自然语言   = 开发目标（等同 /run）；用 @文件 可补全并带入上下文
  /analyze            L1 自分析（只读扫描本仓库，无需 LLM key）
  /improve            L2 给测试缺口生成测试（需 key；build 模式下才写分支）
  /fix <文件,...>     L2.2 深审并外科修复指定文件（需 key；build 模式下才写分支）
  /run <目标>         跑开发循环（dev→test→review，流式）
  /agents             列出已创建的 agent（网页/API 建的，同一份存储）
  /runagent <id> <任务>  用某个已创建的 agent 执行任务（流式）
  /sessions           列出历史会话
  /resume <id>        恢复某个历史会话
  /new                新开一个会话
  /mode               切换 plan(只读/提案) / build(可写分支)
  /clear              清屏
  /help               显示本帮助
  /quit               退出（也可 Ctrl+C）
键位: Tab=切模式  Esc=取消当前操作  Ctrl+L=清屏  Ctrl+C=退出
补全: 输入 / 补全命令、@ 补全文件（→ 接受）"""

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
        # 1) slash 命令补全（输入以 / 开头且还没输到空格）
        if value.startswith("/") and " " not in value:
            vl = value.lower()
            return next((c for c in SLASH_COMMANDS if c.startswith(vl) and c != vl), None)
        # 2) @文件补全
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


class ConfirmScreen(ModalScreen[bool]):
    """写分支前的确认弹窗（对齐 opencode 的权限确认 / 项目“人在关口”理念）。"""

    CSS = """
    ConfirmScreen { align: center middle; }
    #dialog { width: 64; height: auto; border: thick $warning; background: $surface; padding: 1 2; }
    #confirm-hint { color: $text-muted; margin-top: 1; }
    """
    BINDINGS = [
        Binding("y", "yes", "确认"),
        Binding("n", "no", "取消"),
        Binding("escape", "no", "取消"),
    ]

    def __init__(self, message: str):
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self._message, id="confirm-msg")
            yield Static("[y] 确认    [n]/Esc 取消", id="confirm-hint")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


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
        Binding("escape", "cancel", "取消"),
        Binding("tab", "toggle_mode", "切模式"),
        Binding("ctrl+l", "clear_log", "清屏"),
    ]

    def __init__(self, repo_root: str = "."):
        super().__init__()
        self.repo_root = repo_root
        self.mode = "plan"                  # plan | build
        self.transcript: list[str] = []     # 完整记录，便于回看与测试
        # 会话持久化（SQLite）：对话落盘，可 /sessions 列出、/resume 恢复
        self.sessions = SessionManager(str(Path(repo_root) / ".auto-dev-crew" / "sessions.db"))
        self.session_id: str | None = None
        self._persist_on = False            # 开场白阶段先不落盘
        self._busy = False                  # 是否有长任务在跑

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
        self._chrome("[dim]/sessions 查看历史 · /resume <id> 恢复 · /new 新开[/dim]")
        self._sync_subtitle()
        self.session_id = self.sessions.start_session()
        self._persist_on = True            # 之后的对话才落盘（不存开场白）
        self.query_one("#prompt", Input).focus()

    # ---------------------------------------------------------------- 输出
    def _chrome(self, markup: str) -> None:
        """UI 提示（解析 Rich 标记）。"""
        self.transcript.append(markup)
        self.query_one("#log", RichLog).write(markup)
        self._persist(markup, markup=True)

    def _emit(self, text: str) -> None:
        """工具输出（按字面写，避免 [xxx] 被当成标记解析）。"""
        self.transcript.append(text)
        self.query_one("#log", RichLog).write(Text(text))
        self._persist(text, markup=False)

    def _persist(self, content: str, markup: bool) -> None:
        """把一条对话写进当前会话（落盘）。失败不影响交互。"""
        if not self._persist_on:
            return
        try:
            self.sessions.add_message("assistant", content, metadata={"markup": markup})
        except Exception:  # noqa: BLE001
            pass

    def _sync_subtitle(self) -> None:
        desc = "只读/提案" if self.mode == "plan" else "可写分支"
        sid = f" · 会话 {self.session_id}" if self.session_id else ""
        busy = " · ⏳运行中(Esc 取消)" if self._busy else ""
        self.sub_title = f"模式 {self.mode}（{desc}）{sid}{busy}"

    # 集中管理忙碌态：动作 worker 一进入运行就置忙、结束(成功/失败/取消)即解除
    def on_worker_state_changed(self, event) -> None:
        if getattr(event.worker, "group", None) != "action":
            return
        self._busy = event.state == WorkerState.RUNNING
        self._sync_subtitle()

    def action_cancel(self) -> None:
        if self._busy:
            self.workers.cancel_all()
            self._chrome("[yellow]已取消当前操作[/yellow]")

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
        elif self._busy:
            self._chrome("[yellow]正在处理上一条，Esc 取消或稍候[/yellow]")
        else:
            self._do_run(text)          # 自然语言 = 开发目标

    def _dispatch(self, text: str) -> None:
        parts = text[1:].split(maxsplit=1)
        cmd = parts[0] if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd in ACTION_CMDS and self._busy:
            self._chrome("[yellow]正在处理上一条，Esc 取消或稍候[/yellow]")
            return
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
        elif cmd == "sessions":
            self._cmd_sessions()
        elif cmd == "resume":
            if arg:
                self._cmd_resume(arg)
            else:
                self._chrome("[red]/resume 需要会话 id[/red]，先 /sessions 查看")
        elif cmd == "new":
            self._cmd_new()
        elif cmd == "agents":
            self._cmd_agents()
        elif cmd == "runagent":
            if arg:
                self._do_runagent(arg)
            else:
                self._chrome("[red]用法: /runagent <id> <任务>[/red]")
        else:
            self._chrome(f"[red]未知命令 /{cmd}[/red] · /help 看命令")

    # ---------------------------------------------------------------- 已创建的 agent
    def _cmd_agents(self) -> None:
        from src.agents.manager import AgentManager
        mgr = AgentManager(persist_path=_AGENTS_DB(self.repo_root))
        agents = mgr.list_agents()
        if not agents:
            self._emit("(无已创建的 agent；可在网页或 API 创建)")
            return
        lines = ["已创建的 agent（/runagent <id> <任务> 运行）:"]
        for a in agents:
            off = "" if a.config.is_active else " (停用)"
            lines.append(f"  {a.config.id}  {a.config.name} [{a.config.role}]{off}")
        self._emit("\n".join(lines))

    @work(exclusive=True, group="action")
    async def _do_runagent(self, arg: str) -> None:
        parts = arg.split(maxsplit=1)
        if len(parts) < 2:
            self._chrome("[red]用法: /runagent <id> <任务>[/red]")
            return
        aid, task = parts[0], parts[1]
        from src.agents.manager import AgentManager
        from src.agents.config_agent import build_config_agent

        inst = AgentManager(persist_path=_AGENTS_DB(self.repo_root)).get_agent(aid)
        if not inst:
            self._chrome(f"[red]没有 agent {aid}（/agents 查看）[/red]")
            return
        cfg = inst.config
        self._chrome(f"[cyan]运行 agent「{cfg.name}」：{task}[/cyan]")
        stream = self.query_one("#stream", Static)
        stream.display = True
        buf: list[str] = []

        def on_token(tok: str) -> None:
            buf.append(tok)
            stream.update(Text("".join(buf)[-1500:]))

        try:
            agent = build_config_agent(cfg.name, cfg.role, cfg.system_prompt, cfg.model)
            result = await agent.execute(task, context={"on_token": on_token})
            self._emit(f"「{cfg.name}」结果: {'成功' if result.success else '失败'}")
            if result.output:
                self._emit(str(result.output)[:2000])
            elif result.error:
                self._emit(f"错误: {result.error}")
        except Exception as e:  # noqa: BLE001
            self._emit(f"执行出错: {e}")
        finally:
            stream.update("")
            stream.display = False

    # ---------------------------------------------------------------- 会话
    def _cmd_sessions(self) -> None:
        rows = self.sessions.list_recent_sessions(10)
        if not rows:
            self._emit("(暂无历史会话)")
            return
        lines = ["历史会话（/resume <id> 恢复）:"]
        for r in rows:
            summ = self.sessions.store.get_session_summary(r["id"])
            mark = " ← 当前" if r["id"] == self.session_id else ""
            lines.append(f"  {r['id']}  {(r.get('updated_at') or '')[:19]}  消息 {summ.get('messages', 0)}{mark}")
        self._emit("\n".join(lines))

    def _cmd_resume(self, sid: str) -> None:
        if not self.sessions.resume_session(sid):
            self._chrome(f"[red]没有会话 {sid}[/red]")
            return
        self.session_id = sid
        msgs = self.sessions.get_messages(500)
        self.query_one("#log", RichLog).clear()
        self.transcript.clear()
        self._persist_on = False            # 回放期间不重复落盘
        self._chrome(f"[green]已恢复会话 {sid}（{len(msgs)} 条）[/green]")
        for m in msgs:
            try:
                md = json.loads(m.get("metadata") or "{}")
            except Exception:  # noqa: BLE001
                md = {}
            if md.get("markup"):
                self._chrome(m["content"])
            else:
                self._emit(m["content"])
        self._persist_on = True

    def _cmd_new(self) -> None:
        self.session_id = self.sessions.start_session()
        self.query_one("#log", RichLog).clear()
        self.transcript.clear()
        self._chrome(f"[green]已新建会话 {self.session_id}[/green]")

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
    async def _confirm_apply(self, loop, result, what: str) -> None:
        """build 模式下，写分支前弹确认；确认才 apply。"""
        n = len(result.accepted)
        ok = await self.push_screen_wait(
            ConfirmScreen(f"build 模式：把 {n} 项{what}写入一个新分支？（不会碰 main）")
        )
        if ok:
            branch = loop.apply(result)
            self._chrome(f"[green]已写入分支 {branch}（请 review 后合并）[/green]")
        else:
            self._chrome("[yellow]已取消写入（保留为提案）[/yellow]")

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
                await self._confirm_apply(loop, result, "测试")
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
                await self._confirm_apply(loop, result, "修复")
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
