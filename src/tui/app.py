"""VortoCode 交互式全屏 TUI（仿 opencode 的形）。

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
import time
from pathlib import Path

from rich.markdown import Markdown
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
    "/analyze", "/improve", "/fix", "/run", "/apply", "/agents", "/runagent", "/skills", "/mcp",
    "/artifacts", "/diff", "/sessions", "/resume", "/new", "/mode", "/theme", "/usage",
    "/tools", "/audit", "/speak", "/clear", "/help", "/quit",
]
# 命令 → 一句话说明（命令补全面板用，让 / 命令可发现、可补全）
COMMAND_INFO = {
    "/analyze": "L1 自分析（只读扫描，无需 key）",
    "/improve": "L2 给测试缺口生成测试（需 key·build）",
    "/fix": "深审并外科修复指定文件（需 key·build）",
    "/run": "跑开发循环 dev→test→review（流式）",
    "/apply": "把 /run 产出（工作区代码）带 diff 应用到仓库",
    "/agents": "列出已创建的 agent",
    "/runagent": "用某个已创建 agent 执行任务",
    "/skills": "列出 SKILL.md 技能（reload 重扫）",
    "/mcp": "接入 MCP 服务器工具（build 门控）",
    "/artifacts": "列出已发布的制品（画廊在 /artifacts）",
    "/diff": "看工作区改动（git diff，着色）",
    "/sessions": "列出历史会话",
    "/resume": "恢复某个历史会话",
    "/new": "新开一个会话",
    "/mode": "切换 plan / build 模式",
    "/theme": "切换配色主题（21 套内置，记住选择）",
    "/usage": "本会话 token 用量（reset 清零）",
    "/tools": "列出主 agent 工具及读写权限",
    "/audit": "查看工具调用审计日志",
    "/speak": "朗读 agent 回复开关（mimo-v2.5-tts，需 key）",
    "/clear": "清屏",
    "/help": "显示帮助",
    "/quit": "退出",
}
ACTION_CMDS = {"analyze", "improve", "fix", "run", "apply", "runagent", "mcp"}   # 跑长任务，受忙碌态约束

# 工作中指示器（仿 Claude Code）：10 帧 braille 旋转 + 轮换动词 + 计时 + esc 中断
_SPIN_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPIN_VERBS = ["思考中", "琢磨中", "检索中", "运转中", "推敲中"]

# 终端转义/控制序列清洗。为支持中文输入关掉了 kitty 协议后，修饰键（如 Shift+Enter）的
# CSI 序列会漏进输入框：既弄脏显示，其中的 ESC 控制符发到中转站还会让 API 因"非法字符"报错
# （表现为空的"对话出错:"）。提交/输入时一律剔除这些序列与残留控制符。
_CTRL_SEQ_RE = re.compile(
    r"\x1b\[[0-9;:?]*[ -/]*[@-~]"   # 完整 CSI（带 ESC）：如 \x1b[27;2;13~
    r"|\x1b[]P^_X].*?(?:\x07|\x1b\\)"  # OSC/DCS/PM/APC（带终止符）
    r"|\x1b."                        # 其它 ESC 序列（含落单 ESC）
    r"|\[[0-9;:]+[~u]"              # ESC 被剥掉后漏出的修饰键 CSI 体：如 [27;2;13~ / [..u
)


def _sanitize_input(s: str) -> str:
    """剔除漏进输入的终端转义/控制序列，返回干净文本（普通可见字符 + 空格/Tab）。"""
    s = _CTRL_SEQ_RE.sub("", s)
    return "".join(ch for ch in s if ch >= " " or ch == "\t")

_AGENTS_DB = lambda root: str(Path(root) / ".vortocode" / "web_advanced_agents.json")

HELP = """可用命令:
  直接输入自然语言   和主 agent 对话：答疑/读代码/扫描仓库，需要时调起 dev→test→review 开发（仅 build；/run 可强制）；@文件 / @artifact:<id> 带入上下文
  /analyze            L1 自分析（只读扫描本仓库，无需 LLM key）
  /improve            L2 给测试缺口生成测试（需 key；build 模式下才写分支）
  /fix <文件,...>     L2.2 深审并外科修复指定文件（需 key；build 模式下才写分支）
  /run <目标>         跑开发循环（dev→test→review，流式）
  /apply              把上次 /run 的产出（工作区代码）带 diff+确认应用到仓库（闭合"实现→落地"）
  /skills [reload]    列出 SKILL.md 技能（reload 重新扫描）
  /tools              列出主 agent 可用工具及其读写权限
  /audit              查看工具调用审计日志（.vortocode/audit.log）
  /artifacts          列出已发布的制品（标题/版本/链接；浏览器开 /artifacts 是画廊）
  /diff               看工作区改动（git diff，+绿/-红着色）—— review 主 agent 改了什么
  /mcp [list|off]     接入 config/mcp.yaml 的 MCP 服务器工具（build 门控）
  /agents             列出已创建的 agent（网页/API 建的，同一份存储）
  /runagent <id> <任务>  用某个已创建的 agent 执行任务（流式）
  /sessions           列出历史会话
  /resume <id>        恢复某个历史会话
  /new                新开一个会话
  /mode               切换 plan(只读/提案) / build(可写分支)
  /theme [名]         切换配色主题（不带名=列出全部；选择会记住，下次自动用）
  /usage [reset]      本会话 token 用量（估算；reset 清零）
  /clear              清屏
  /help               显示本帮助
  /quit               退出（也可 Ctrl+C）
键位: Tab=补全/切模式  ↑↓=翻输入历史  Esc=取消  Ctrl+L=清屏  Ctrl+C=退出
补全: 输入 / 列命令、@ 列文件，上方面板高亮首选，Tab 或 → 接受；继续输入可筛选"""

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
        Binding("a", "always", "始终允许"),
        Binding("n", "no", "取消"),
        Binding("escape", "no", "取消"),
    ]

    def __init__(self, message: str):
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self._message, id="confirm-msg")
            yield Static("[y] 确认    [a] 本会话始终允许    [n]/Esc 取消", id="confirm-hint")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_always(self) -> None:
        """本会话内后续写操作不再逐个确认（对齐 Claude Code 的 Always allow）。"""
        try:
            self.app._allow_writes_session = True
        except Exception:  # noqa: BLE001
            pass
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class VortoCodeTUI(App):
    """仿 opencode 的交互式开发 TUI。"""

    TITLE = "VortoCode"
    CSS = """
    #log { height: 1fr; border: round $accent; padding: 0 1; }
    #stream { max-height: 10; overflow-y: auto; color: $text-muted; padding: 0 1;
              border-left: solid $success; }
    #status { height: 1; color: $text-muted; padding: 0 1; }
    #palette { height: auto; max-height: 9; overflow-y: auto; background: $surface;
               color: $text-muted; padding: 0 1; }
    #prompt { border: round $panel; }
    #prompt:focus { border: round $accent; }
    """
    BINDINGS = [
        Binding("ctrl+c", "quit", "退出", priority=True),
        Binding("escape", "cancel", "取消"),
        Binding("tab", "toggle_mode", "补全/切模式"),
        Binding("up", "history_prev", "上一条", show=False),
        Binding("down", "history_next", "下一条", show=False),
        Binding("ctrl+l", "clear_log", "清屏"),
    ]

    def __init__(self, repo_root: str = "."):
        super().__init__()
        self.repo_root = repo_root
        self.mode = "plan"                  # plan | build
        self.transcript: list[str] = []     # 完整记录，便于回看与测试
        # 会话持久化（SQLite）：对话落盘，可 /sessions 列出、/resume 恢复
        self.sessions = SessionManager(str(Path(repo_root) / ".vortocode" / "sessions.db"))
        self.session_id: str | None = None
        self._persist_on = False            # 开场白阶段先不落盘
        self._busy = False                  # 是否有长任务在跑
        self.agent = None                   # 主 agent loop（首次用到时惰性构建）
        self._skills = None                 # SkillRegistry（惰性构建、可 /skills reload）
        self._mcp = None                    # ToolManager（/mcp 连接后才有）
        self._mcp_tools: list = []          # 已接入的 MCP 工具（包成主 agent 的 Tool）
        self._spin_i = 0                    # 工作指示器：帧/计时/定时器
        self._busy_since = 0.0
        self._spin_timer = None
        self._history: list[str] = []       # 提交过的输入（↑/↓ 调出，仿 shell；跨会话持久化）
        self._history_idx: int | None = None
        self._history_draft = ""            # 进入历史浏览前的草稿，↓ 到底恢复
        self._allow_writes_session = False  # 本会话"始终允许"写操作（ConfirmScreen 的 [a]）
        self._speak_replies = False         # /speak 开关：开则把每条回复合成语音朗读（mimo-v2.5-tts）
        self._turn_tools = 0                # 本回合工具调用计数（回合结束给"✓ 完成"反馈）
        self._last_dev = None              # 最近一次 dev 流水线产出 {workspace, files}，供 /apply

    # ---------------------------------------------------------------- 布局
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="log", wrap=True, markup=True, highlight=False, auto_scroll=True)
        yield Static(id="stream")
        yield Static(id="status")
        yield Static(id="palette")
        yield Input(placeholder="输入需求（自然语言），或 / 看命令…",
                    id="prompt", suggester=FileSuggester(self.repo_root))
        yield Footer()

    def on_mount(self) -> None:
        from src.llm.client import reset_usage
        reset_usage()                       # 每个会话从零计量
        self._load_history()                # 跨会话输入历史（↑/↓ 可调出上次的）
        self._load_theme()                  # 套用上次选的配色主题
        self.query_one("#stream", Static).display = False
        self.query_one("#status", Static).display = False
        self.query_one("#palette", Static).display = False
        self._greet()
        self._sync_subtitle()
        self.session_id = self.sessions.start_session()
        self._persist_on = True            # 之后的对话才落盘（不存开场白）
        self.query_one("#prompt", Input).focus()

    def _greet(self) -> None:
        """首跑引导：能力速览 + 示例 + plan/build 说明 + 无 key 提示，让新用户立刻知道能干嘛。"""
        import os
        self._chrome("[b]VortoCode[/b] · 交互式 AI 开发助手 "
                     "[dim](一个会话主 agent，自己分流：答疑 / 读代码 / 动手开发)[/dim]")
        self._chrome("[dim]能做：问答 · 读&搜代码(@文件) · 看图(@图片.png) · 听音频(@音频.mp3) · "
                     "朗读回复(/speak) · 扫描仓库问题 · 实现/修改/测试代码 · 发布可分享制品[/dim]")
        self._chrome("")
        self._chrome(f"[{self._tc('text-primary', '#8ab4f8')}]试试：[/] "
                     "[b]这个项目是做什么的？[/b]   ·   "
                     "[b]@src/cli.py 讲讲这个文件[/b]   ·   [b]给 xx 模块补测试[/b]")
        self._chrome("[dim]模式：plan=只读/提案（默认更稳），build=可写新分支（绝不碰 main）。"
                     "要它动手时会问你切不切，[b]不用先手动切[/b]。[/dim]")
        self._chrome("[dim]键位：输入 [b]/[/b] 或 [b]@[/b] 看补全 · Tab/→ 接受 · ↑↓ 翻历史 · "
                     "Tab 切模式 · [b]/help[/b] 全部命令[/dim]")
        if not os.getenv("OPENAI_API_KEY"):
            self._chrome("[yellow]⚠ 未配置 OPENAI_API_KEY[/yellow][dim] —— 对话/开发需要它："
                         "在项目根 [b].env[/b] 写 OPENAI_API_KEY=sk-... 后重启即可；"
                         "无 key 也能用 [b]/analyze[/b] 扫描、[b]/help[/b]。[/dim]")

    # ---------------------------------------------------------------- 输出
    def _chrome(self, markup: str) -> None:
        """UI 提示（解析 Rich 标记）。"""
        self.transcript.append(markup)
        self.query_one("#log", RichLog).write(markup)
        self._persist(markup, markup=True)

    def _emit(self, text: str) -> None:
        """工具/命令输出（按字面写，避免 [xxx] 被当成标记解析）。"""
        self.transcript.append(text)
        self.query_one("#log", RichLog).write(Text(text))
        self._persist(text, markup=False)

    def _tc(self, name: str, fallback: str) -> str:
        """取当前主题的某个语义色（Rich 文本用），拿不到/未挂载用 fallback —— 让消息色随主题。"""
        try:
            return self.theme_variables.get(name) or fallback
        except Exception:  # noqa: BLE001
            return fallback

    def _say_user(self, text: str) -> None:
        """用户输入回显：醒目、turn 间留空行，和系统提示/助手回复区分开。"""
        log = self.query_one("#log", RichLog)
        log.write("")                       # turn 之间留白，避免糊成一片
        t = Text()
        t.append("❯ ", style=f"bold {self._tc('text-primary', '#8ab4f8')}")
        t.append(text, style=self._tc("text", "#cdd6f4"))
        log.write(t)
        self.transcript.append(text)
        self._persist(text, markup=False)

    def _assistant(self, text: str) -> None:
        """主 agent 最终回复：● 署名一行 + 正文（markdown 渲染，失败回退纯文本）。"""
        log = self.query_one("#log", RichLog)
        head = Text()
        head.append("● ", style=f"bold {self._tc('text-success', '#7fce9a')}")
        head.append("vorto", style="dim italic")
        log.write(head)
        try:
            log.write(Markdown(text) if text.strip() else Text("(无回复)"))
        except Exception:  # noqa: BLE001
            log.write(Text(text))            # markdown 渲染异常不致命，退回纯文本
        log.write("")                        # turn 间留白
        self.transcript.append(text)
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
        allow = " · 写:始终允许✓" if self._allow_writes_session else ""
        from src.llm.client import get_usage
        u = get_usage()
        tot = u["total_tokens"]
        tok = (f" · ~{tot // 1000}k tok/{u['calls']}call" if tot >= 1000
               else f" · ~{tot} tok/{u['calls']}call") if u["calls"] else ""
        self.sub_title = f"模式 {self.mode}（{desc}）{sid}{busy}{allow}{tok}"

    # 集中管理忙碌态：动作 worker 一进入运行就置忙、结束(成功/失败/取消)即解除
    def on_worker_state_changed(self, event) -> None:
        if getattr(event.worker, "group", None) != "action":
            return
        running = event.state == WorkerState.RUNNING
        self._busy = running
        self._start_status() if running else self._stop_status()
        self._sync_subtitle()

    # ---------------------------------------------------------------- 工作指示器
    def _start_status(self) -> None:
        """开始转圈（braille 旋转 + 计时 + esc 中断），仿 Claude Code 的"思考中"。"""
        self._busy_since = time.monotonic()
        self.query_one("#status", Static).display = True
        if self._spin_timer is None:
            self._spin_timer = self.set_interval(0.1, self._tick_status)
        self._tick_status()

    def _stop_status(self) -> None:
        if self._spin_timer is not None:
            self._spin_timer.stop()
            self._spin_timer = None
        try:
            self.query_one("#status", Static).display = False
        except Exception:  # noqa: BLE001
            pass

    def _tick_status(self) -> None:
        self._spin_i += 1
        elapsed = int(time.monotonic() - self._busy_since)
        frame = _SPIN_FRAMES[self._spin_i % len(_SPIN_FRAMES)]
        verb = _SPIN_VERBS[(self._spin_i // 12) % len(_SPIN_VERBS)]   # 约 1.2s 换一个词
        warn = self._tc("text-warning", "#f0b86e")
        t = Text()
        t.append(f"{frame} ", style=warn)
        t.append(f"{verb} ", style=warn)
        if self._turn_tools:                       # 工具数随回合增长 → 一眼看出在推进
            t.append(f"· {self._turn_tools} 工具 ", style="dim")
        t.append(f"{elapsed}s", style="dim")
        t.append("  ·  esc 中断", style="dim")
        try:
            self.query_one("#status", Static).update(t)
        except Exception:  # noqa: BLE001
            pass

    def action_cancel(self) -> None:
        if self._busy:
            self.workers.cancel_all()
            self._chrome("[yellow]已取消当前操作[/yellow]")

    # ---------------------------------------------------------------- 键位动作
    def action_toggle_mode(self) -> None:
        # Tab 上下文化：在敲 / 命令或 @文件且能补全 → 补全到首个匹配；否则切 plan/build 模式。
        v = self.query_one("#prompt", Input).value
        if v.startswith("/") and " " not in v:
            matches = [c for c in SLASH_COMMANDS if c.startswith(v.lower())]
            if matches and matches[0].lower() != v.lower():
                self._set_input(matches[0])
                return
        at = v.rfind("@")
        if at != -1 and " " not in v[at:] and ":" not in v[at:]:
            tl = v[at + 1:].lower()
            files = _repo_files(self.repo_root)
            cand = (next((f for f in files if f.lower().startswith(tl)), None)
                    or next((f for f in files if tl in f.lower()), None))
            if cand and v[at + 1:] != cand:
                self._set_input(v[:at + 1] + cand)
                return
        self.mode = "build" if self.mode == "plan" else "plan"
        self._sync_subtitle()
        self._chrome(f"→ 切到 [b]{self.mode}[/b] 模式")

    def _set_input(self, val: str) -> None:
        inp = self.query_one("#prompt", Input)
        inp.value = val
        inp.cursor_position = len(val)

    async def _confirm_write(self, message: str) -> bool:
        """写操作确认门：本会话已选"始终允许"则直接放行，否则弹 ConfirmScreen。

        统一所有写工具(edit/write/save_skill/制品/分支)的确认，支持 [a] 始终允许（仿 CC）。
        """
        if self._allow_writes_session:
            return True
        return await self.push_screen_wait(ConfirmScreen(message))

    async def _confirm_outward(self, message: str) -> bool:
        """外向操作（push / 开 PR 等推到远端的动作）确认：**始终弹窗**，不吃"始终允许写"的豁免。"""
        return await self.push_screen_wait(ConfirmScreen(message))

    def action_history_prev(self) -> None:
        """↑：调出上一条历史输入（编辑过则当作新输入，从末尾重新起）。"""
        if not self._history:
            return
        inp = self.query_one("#prompt", Input)
        if self._history_idx is not None and inp.value != self._history[self._history_idx]:
            self._history_idx = None
        if self._history_idx is None:
            self._history_draft = inp.value
            self._history_idx = len(self._history)
        if self._history_idx > 0:
            self._history_idx -= 1
            self._set_input(self._history[self._history_idx])

    def action_history_next(self) -> None:
        """↓：回到下一条历史；到底则恢复草稿。"""
        if self._history_idx is None:
            return
        inp = self.query_one("#prompt", Input)
        if inp.value != self._history[self._history_idx]:
            self._history_idx = None
            return
        if self._history_idx < len(self._history) - 1:
            self._history_idx += 1
            self._set_input(self._history[self._history_idx])
        else:
            self._history_idx = None
            self._set_input(self._history_draft)

    def _history_file(self) -> Path:
        return Path(self.repo_root) / ".vortocode" / "tui_history"

    def _load_history(self) -> None:
        """启动时载入跨会话输入历史（取尾部 200 条）。失败不影响。"""
        try:
            p = self._history_file()
            if p.is_file():
                self._history = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln][-200:]
        except Exception:  # noqa: BLE001
            pass

    def _append_history(self, text: str) -> None:
        """把一条提交追加到历史文件（单行；跨会话持久）。失败不影响。"""
        try:
            p = self._history_file()
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(text.replace("\n", " ") + "\n")
        except Exception:  # noqa: BLE001
            pass

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()

    # ---------------------------------------------------------------- 输入分发
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = _sanitize_input(event.value).strip()   # 剔除漏进的终端转义序列，防脏字符进 agent/API
        self.query_one("#prompt", Input).value = ""
        self.query_one("#palette", Static).display = False
        self._history_idx = None                # 提交后退出历史浏览
        if not text:
            return
        if not self._history or self._history[-1] != text:
            self._history.append(text)          # 记入输入历史（去重相邻）
            self._append_history(text)          # 跨会话持久化
        self._say_user(text)
        if text.startswith("/"):
            self._dispatch(text)
        elif self._busy:
            self._chrome("[yellow]正在处理上一条，Esc 取消或稍候[/yellow]")
        else:
            self._route(text)           # 普通话：先判意图（闲聊/提问 vs 开发需求）再分流

    def on_input_changed(self, event: Input.Changed) -> None:
        """输入变化时更新命令补全面板（输入以 / 开头即可见）；并当场抹掉漏进的终端转义序列。"""
        clean = _sanitize_input(event.value)
        if clean != event.value:                 # 修饰键 CSI 等漏进来了 → 立即清掉，别弄脏显示
            inp = self.query_one("#prompt", Input)
            inp.value = clean
            inp.cursor_position = len(clean)
            return                               # 重设会再触发 Changed，那次已干净
        self._update_palette(event.value)

    def _update_palette(self, value: str) -> None:
        """输入框上方列出补全候选、高亮首选（Tab/→ 接受）：/ → 命令；@ → 仓库文件。否则隐藏。"""
        s = value.strip()
        if s.startswith("/") and " " not in s:                 # 斜杠命令
            vl = s.lower()
            matches = [c for c in SLASH_COMMANDS if c.startswith(vl)]
            if matches:
                self._render_palette([(c, COMMAND_INFO.get(c, "")) for c in matches], "命令")
                return
        at = value.rfind("@")                                  # @文件（非 @artifact: 这种带冒号的）
        if at != -1:
            token = value[at + 1:]
            if " " not in token and ":" not in token:
                tl = token.lower()
                files = _repo_files(self.repo_root)
                hits = ([f for f in files if f.lower().startswith(tl)]
                        or [f for f in files if tl in f.lower()])
                if hits:
                    self._render_palette([(f, "") for f in hits[:8]], "文件")
                    return
        self.query_one("#palette", Static).display = False

    def _render_palette(self, items: list, kind: str) -> None:
        """渲染补全面板：items=[(text, desc)]，首项高亮（= Tab/→ 接受目标）。"""
        rows = []
        for i, (txt, desc) in enumerate(items[:8]):
            d = f"  [dim]{desc}[/dim]" if desc else ""
            mark = self._tc("text-primary", "#8ab4f8")
            rows.append(f"[b {mark}]›[/] [b]{txt}[/b]{d}" if i == 0 else f"  {txt}{d}")
        more = f" · +{len(items) - 8} 更多" if len(items) > 8 else ""
        rows.append(f"[dim]{kind}补全 · Tab/→ 接受首选 · 继续输入筛选{more}[/dim]")
        palette = self.query_one("#palette", Static)
        palette.update(Text.from_markup("\n".join(rows)))
        palette.display = True

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
        elif cmd == "apply":
            self._do_apply()
        elif cmd == "sessions":
            self._cmd_sessions()
        elif cmd == "resume":
            if arg:
                self._cmd_resume(arg)
            else:
                self._chrome("[red]/resume 需要会话 id[/red]，先 /sessions 查看")
        elif cmd == "new":
            self._cmd_new()
        elif cmd == "skills":
            self._cmd_skills(arg)
        elif cmd == "theme":
            self._cmd_theme(arg)
        elif cmd == "usage":
            self._cmd_usage(arg)
        elif cmd == "tools":
            self._cmd_tools()
        elif cmd == "audit":
            self._cmd_audit(arg)
        elif cmd == "artifacts":
            self._cmd_artifacts()
        elif cmd == "diff":
            self._cmd_diff()
        elif cmd == "mcp":
            self._cmd_mcp(arg)
        elif cmd == "agents":
            self._cmd_agents()
        elif cmd == "speak":
            self._cmd_speak(arg)
        elif cmd == "runagent":
            if arg:
                self._do_runagent(arg)
            else:
                self._chrome("[red]用法: /runagent <id> <任务>[/red]")
        else:
            self._chrome(f"[red]未知命令 /{cmd}[/red] · /help 看命令")

    # ---------------------------------------------------------------- 技能（SKILL.md）
    def _cmd_skills(self, arg: str) -> None:
        """/skills 列出技能；/skills reload 重新扫描并让主 agent 下次重建。"""
        if arg.strip() == "reload":
            reg = self._skill_registry(reload=True)
            self.agent = None               # 让主 agent 下次重建、拿到新技能目录
            self._chrome(f"[green]已重载技能（{len(reg.skills)} 个）；下次对话生效[/green]")
            return
        reg = self._skill_registry()
        if not reg.skills:
            self._emit("(没有技能。把 SKILL.md 放到 skills/<名>/ 或 .vortocode/skills/<名>/ 后 /skills reload)")
            return
        lines = ["可用技能（对话里说\"用 X 技能\"，或主 agent 自动选用；/skills reload 重载）:"]
        for s in reg.skills.values():
            lines.append(f"  {s.name} — {s.description}")
        self._emit("\n".join(lines))

    def _cmd_tools(self) -> None:
        """列出主 agent 可用工具（plan 只读可用；写/重型仅 build）。"""
        agent = self.agent or self._build_main_agent()
        lines = ["主 agent 工具（plan 只读可用；写/重型仅 build）:"]
        for t in agent._tool_list:
            gate = "只读" if t.read_only else "写/重型"
            lines.append(f"  {t.name} [{gate}] — {t.description}")
        self._emit("\n".join(lines))

    def _cmd_audit(self, arg: str) -> None:
        """/audit 看最近的工具调用审计（.vortocode/audit.log）。"""
        p = Path(self.repo_root) / ".vortocode" / "audit.log"
        if not p.is_file():
            self._emit("(暂无审计记录；主 agent 调用工具后才有)")
            return
        lines = p.read_text(encoding="utf-8").splitlines()
        self._emit(f"工具调用审计（共 {len(lines)} 条，显示最近 15）:\n" + "\n".join(lines[-15:]))

    def _cmd_artifacts(self) -> None:
        """/artifacts 列出已发布的制品（标题/版本/类型/链接）。需起 Web 服务器才能打开。"""
        from src.web.artifacts import ArtifactStore, artifact_url
        items = ArtifactStore(self.repo_root).list()
        if not items:
            self._emit("(还没有制品。build 模式下让主 agent publish_artifact，"
                       "或说\"把这个做成可分享的页面\")")
            return
        lines = [f"已发布制品（共 {len(items)}；浏览器开 /artifacts 是画廊，需先起 Web 服务器）:"]
        for m in items:
            lines.append(f"  {m['title']} [v{m['version']} · {m.get('kind', 'html')}] "
                         f"— {artifact_url(None, m['id'])}")
        lines.append("提示：对话里 @artifact:<id> 可把某制品当前内容带给主 agent 迭代。")
        self._emit("\n".join(lines))

    def _cmd_diff(self) -> None:
        """/diff：把工作区改动（git diff）着色渲染出来，方便 review 主 agent 改了什么。"""
        import subprocess
        try:
            r = subprocess.run(["git", "diff"], cwd=self.repo_root,
                               capture_output=True, text=True, timeout=15)
        except Exception as e:  # noqa: BLE001
            self._emit(f"git diff 失败: {e}（不是 git 仓库？）")
            return
        diff = r.stdout or ""
        if not diff.strip():
            self._emit("(工作区无未提交改动；git diff 为空)")
            return
        self._chrome("[dim]工作区改动（git diff）:[/dim]")
        self._render_diff_text(diff, max_lines=400)

    def _cmd_speak(self, arg: str = "") -> None:
        """/speak：朗读 agent 回复的开关（on/off 可显式指定，否则切换）。开了之后每条回复合成语音播放。"""
        a = (arg or "").strip().lower()
        if a in ("on", "开"):
            self._speak_replies = True
        elif a in ("off", "关"):
            self._speak_replies = False
        else:
            self._speak_replies = not self._speak_replies
        if self._speak_replies:
            self._chrome("[green]🔊 朗读已开[/green][dim]（每条回复用 mimo-v2.5-tts 合成播放；再 /speak 关闭）[/dim]")
        else:
            self._chrome("[dim]🔊 朗读已关[/dim]")

    def _play_audio_file(self, path: str) -> bool:
        """best-effort 播放 WAV：afplay/aplay/ffplay 第一个可用的后台播；找不到返回 False。"""
        import shutil
        import subprocess
        for player, flags in (("afplay", []), ("aplay", ["-q"]),
                              ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"])):
            if shutil.which(player):
                try:
                    subprocess.Popen([player, *flags, path],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
                except Exception:  # noqa: BLE001
                    return False
        return False

    async def _speak_text(self, text: str) -> None:
        """把一段文字合成成语音并播放（/speak 开时对每条回复调用）。失败只提示、不打断对话。"""
        text = (text or "").strip()
        if not text:
            return
        try:
            import os
            import tempfile

            from src.llm.client import LLMClient
            wav = await LLMClient().tts(text)
        except Exception as e:  # noqa: BLE001
            self._chrome(f"[dim]🔊 朗读失败：{e}[/dim]")
            return
        try:
            path = os.path.join(tempfile.gettempdir(), "vorto_tui_reply.wav")
            with open(path, "wb") as f:
                f.write(wav)
        except OSError as e:
            self._chrome(f"[dim]🔊 写语音文件失败：{e}[/dim]")
            return
        if not self._play_audio_file(path):
            self._chrome("[dim]🔊 已合成语音，但没找到可用播放器（afplay/aplay/ffplay）[/dim]")

    # ---------------------------------------------------------------- MCP 工具接入
    def _wrap_mcp_tools(self) -> list:
        """把已连接的 MCP server 工具包成主 agent 的 Tool。

        命名 mcp__<server>__<tool> 防冲突；外部工具一律 build 门控（read_only=False，人在关口）。
        """
        from src.agents.main_agent import Tool
        wrapped = []
        for mt in self._mcp.list_tools():
            orig = mt.name
            server = getattr(mt, "server_name", "") or "mcp"
            props = (getattr(mt, "input_schema", None) or {}).get("properties", {}) or {}
            targs = {k: str(v.get("description") or v.get("type") or "") for k, v in props.items()}

            async def handler(a: dict, _orig=orig) -> str:
                res = await self._mcp.execute_tool(_orig, a)
                if getattr(res, "success", True):
                    return str(getattr(res, "output", res))
                return f"MCP 工具出错: {getattr(res, 'error', res)}"

            wrapped.append(Tool(f"mcp__{server}__{orig}",
                                f"[MCP:{server}] {mt.description}", targs, handler, read_only=False))
        return wrapped

    @work(exclusive=True, group="action")
    async def _cmd_mcp(self, arg: str) -> None:
        """/mcp 连接 config/mcp.yaml 的 MCP 服务器；/mcp list 列出；/mcp off 断开。"""
        arg = arg.strip()
        if arg in ("off", "disconnect"):
            if self._mcp is not None:
                try:
                    await self._mcp.shutdown()
                except Exception:  # noqa: BLE001
                    pass
            self._mcp, self._mcp_tools, self.agent = None, [], None
            self._chrome("[green]已断开 MCP[/green]")
            return
        if arg == "list":
            if not self._mcp_tools:
                self._emit("(未连接 MCP；/mcp 连接 config/mcp.yaml 里 enabled 的服务器)")
            else:
                self._emit("已接入的 MCP 工具:\n"
                           + "\n".join(f"  {t.name} — {t.description}" for t in self._mcp_tools))
            return
        # 连接
        self._chrome("[cyan]连接 MCP 服务器（config/mcp.yaml）…[/cyan]")
        try:
            from src.tools.manager import ToolManager
            mgr = ToolManager(str(Path(self.repo_root) / "config" / "mcp.yaml"))
            await mgr.initialize()
            self._mcp = mgr
            self._mcp_tools = self._wrap_mcp_tools()
            self.agent = None            # 让主 agent 下次重建、拿到 MCP 工具
            servers = list(getattr(mgr, "mcp_clients", {}).keys())
            self._emit(f"已接入 {len(self._mcp_tools)} 个 MCP 工具，来自服务器: {', '.join(servers) or '（无）'}")
            if not self._mcp_tools:
                self._emit("（没连上工具：检查 config/mcp.yaml 是否 enabled、命令如 npx 是否可用）")
        except Exception as e:  # noqa: BLE001
            self._emit(f"MCP 连接失败: {e}")

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
        self.agent = None                   # 丢掉上个会话的 agent 上下文
        self._chrome(f"[green]已恢复会话 {sid}（{len(msgs)} 条）[/green]")
        last_snapshot = None
        for m in msgs:
            try:
                md = json.loads(m.get("metadata") or "{}")
            except Exception:  # noqa: BLE001
                md = {}
            if md.get("agent_history"):     # agent 上下文快照：不显示，只留最后一份用于重建
                last_snapshot = m["content"]
                continue
            if md.get("markup"):
                self._chrome(m["content"])
            else:
                self._emit(m["content"])
        self._persist_on = True
        self._restore_agent_history(last_snapshot)

    def _restore_agent_history(self, snapshot) -> None:
        """从快照重建 agent 历史，让 /resume 后主 agent 记得之前聊了什么。"""
        if not snapshot:
            return
        try:
            hist = json.loads(snapshot)
        except Exception:  # noqa: BLE001
            return
        if isinstance(hist, list) and hist:
            self.agent = self._build_main_agent()
            self.agent.history = hist
            self._chrome("[dim]↻ 已恢复对话上下文（主 agent 记得之前的对话）[/dim]")

    def _cmd_new(self) -> None:
        from src.llm.client import reset_usage
        self.session_id = self.sessions.start_session()
        self.agent = None                   # 新会话 = 全新 agent 上下文
        self._allow_writes_session = False  # "始终允许"也随新会话复位
        reset_usage()                       # 用量也清零
        self.query_one("#log", RichLog).clear()
        self.transcript.clear()
        self._chrome(f"[green]已新建会话 {self.session_id}[/green]")
        self._sync_subtitle()

    def _cmd_usage(self, arg: str) -> None:
        """/usage 看本会话用量（估算）；/usage reset 清零。"""
        from src.llm.client import get_usage, reset_usage
        if arg.strip() == "reset":
            reset_usage()
            self._sync_subtitle()
            self._chrome("[green]已清零用量计数[/green]")
            return
        u = get_usage()
        self._emit(f"本会话用量（估算）: 调用 {u['calls']} 次 · 输入 ~{u['prompt_tokens']} · "
                   f"输出 ~{u['completion_tokens']} · 合计 ~{u['total_tokens']} tokens")

    def _cmd_theme(self, arg: str) -> None:
        """/theme：列出/切换配色主题（Textual 内置 21 套）；选择记到 .vortocode/tui_theme。"""
        names = sorted(self.available_themes)
        name = arg.strip()
        if not name:
            cur = self.theme
            rows = "  ".join(f"[b]{n}[/b]" if n == cur else n for n in names)
            self._emit(f"当前主题: {cur}\n可用（/theme <名> 切换，会记住）:\n{rows}")
            return
        if name not in self.available_themes:
            self._emit(f"没有主题「{name}」。/theme 看全部。")
            return
        self.theme = name                   # Textual 响应式：立刻重绘 chrome（边框/面板/底色）
        self._persist_theme(name)
        self._chrome(f"[green]→ 主题切到 {name}（已记住）[/green]")

    def _theme_file(self) -> Path:
        return Path(self.repo_root) / ".vortocode" / "tui_theme"

    def _persist_theme(self, name: str) -> None:
        try:
            p = self._theme_file()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(name, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def _load_theme(self) -> None:
        """启动时套用上次选的主题。失败/不存在则用默认。"""
        try:
            p = self._theme_file()
            if p.is_file():
                name = p.read_text(encoding="utf-8").strip()
                if name in self.available_themes:
                    self.theme = name
        except Exception:  # noqa: BLE001
            pass

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

    def _expand_context(self, text: str) -> tuple[str, str]:
        """把 @提及 展开成上下文注入主 agent：@文件→内容；@目录→文件清单；@符号→AST 定义位置。

        返回 (去掉 @ 的文本, 拼好的上下文串)；没解析到的 @token 原样保留。
        符号用 AST（src.indexing.PythonASTParser）解析，比 grep 准；整次调用只建一次索引。
        """
        from src.llm.content import is_audio_ref, is_image_ref
        base = Path(self.repo_root)
        parts: list[str] = []
        images: list[str] = []                     # @图片 → 多模态附件（不当文本注入）
        audio: list[str] = []                      # @音频 → 多模态附件
        sym_index: dict[str, list[str]] = {}
        index_built: list[bool] = []

        def symbol_index() -> dict[str, list[str]]:
            if not index_built:
                index_built.append(True)
                try:
                    from src.indexing.ast_parser import NodeType, PythonASTParser
                except Exception:  # noqa: BLE001
                    return sym_index
                parser = PythonASTParser()
                for f in _repo_files(self.repo_root):
                    try:
                        nodes = parser.parse_file(base / f)
                    except Exception:  # noqa: BLE001
                        continue
                    for n in nodes:
                        if n.node_type not in (NodeType.CLASS, NodeType.FUNCTION, NodeType.METHOD):
                            continue
                        head = (f"class {n.name}" if n.node_type == NodeType.CLASS
                                else f"def {n.name}({', '.join(n.parameters)})")
                        doc = (n.docstring or "").strip().splitlines()
                        doc0 = f"  — {doc[0][:80]}" if doc else ""
                        sym_index.setdefault(n.name, []).append(f"{f}:{n.location.line}  {head}{doc0}")
            return sym_index

        def repl_artifact(m: "re.Match") -> str:
            aid = m.group(1)
            try:
                from src.web.artifacts import ArtifactStore
                store = ArtifactStore(self.repo_root)
                meta = store.meta(aid)
                content = store.html(aid)
            except Exception:  # noqa: BLE001
                return m.group(0)
            if not meta or content is None:
                return m.group(0)                       # 没这个制品 → 原样保留
            parts.append(
                f"# 制品 {aid}（{meta.get('title', '')}, v{meta.get('version')}, "
                f"{meta.get('kind', 'html')}）的当前内容\n{content[:3000]}")
            return f"制品[{aid}]"

        def repl(m: "re.Match") -> str:
            ref = m.group(1)
            p = base / ref
            if p.is_file():
                if is_image_ref(ref):              # 图片：作为多模态附件交给 agent，不读成文本
                    images.append(str(p))
                    return f"图片[{ref}]"
                if is_audio_ref(ref):              # 音频：同理，作为 input_audio 附件
                    audio.append(str(p))
                    return f"音频[{ref}]"
                try:
                    parts.append(f"# 文件 {ref}\n{p.read_text(encoding='utf-8')[:3000]}")
                    return ref
                except (OSError, UnicodeDecodeError):   # 二进制等读不动 → 原样保留 @token
                    return m.group(0)
            if p.is_dir():
                sub = ref.rstrip("/")
                hits = [f for f in _repo_files(self.repo_root) if f.startswith(sub)][:50]
                parts.append(f"# 目录 {ref} 下的源码文件\n" + ("\n".join(hits) or "(空)"))
                return ref
            if "/" not in ref and "." not in ref:        # 当作符号：AST 找 def/class 定义
                defs = symbol_index().get(ref)
                if defs:
                    parts.append(f"# 符号 {ref} 的定义（AST）\n" + "\n".join(defs[:20]))
                    return ref
            return m.group(0)

        # 先处理 @artifact:<id>（含 ':'，一般 @ 规则匹配不到），再处理 @文件/@目录/@符号
        clean = re.sub(r"@artifact:([A-Za-z0-9_-]+)", repl_artifact, text)
        clean = re.sub(r"@([\w./一-鿿-]+)", repl, clean)
        self._turn_images = images                 # 供本回合 run_turn 取用（@图片）
        self._turn_audio = audio                   # 同上（@音频）
        return clean, "\n\n".join(parts)

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
        ok = await self._confirm_write(
            f"build 模式：把 {n} 项{what}写入一个新分支？（不会碰 main）"
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

    # ---------------------------------------------------------------- 主 agent loop
    @work(exclusive=True, group="action")
    async def _route(self, text: str) -> None:
        """普通话（非 / 命令）入口：交给主 agent loop。

        主 agent 自己决定是聊天/读代码/扫描（只读工具），还是把正经开发任务交给
        run_dev_workflow（重型，build 模式）—— 不再需要前置意图分类器，闲聊天然由它处理。
        """
        import os
        if not os.getenv("OPENAI_API_KEY"):
            self._chrome("[green]对话[/green] [dim](未配置 OPENAI_API_KEY)[/dim]")
            self._emit("配置 OPENAI_API_KEY 后即可自由对话/开发（见 .env）。")
            self._emit("现在无需 key 也能用：/analyze 扫描本仓库、/help 看全部命令。")
            return
        if self.agent is None:
            self.agent = self._build_main_agent()
        user_text, ctx = self._expand_context(text)
        images = getattr(self, "_turn_images", []) or []      # @图片 → 多模态附件
        audio = getattr(self, "_turn_audio", []) or []        # @音频 → 多模态附件
        if ctx:
            self._chrome(f"[dim]＋ 已注入 @提及的上下文（{len(ctx)} 字）[/dim]")
            user_text = f"{user_text}\n\n[@提及的上下文]\n{ctx}"
        if images:
            self._chrome(f"[dim]🖼 附带 {len(images)} 张图（mimo-v2.5 可读图）[/dim]")
        if audio:
            self._chrome(f"[dim]🎧 附带 {len(audio)} 段音频（mimo-v2.5 可听音频）[/dim]")
        # agent 的输出直接落进结果区（对话 log）：忙时转圈("思考中…")给进度反馈，工具调用与
        # 结果(🔧/⎿)实时进 log，回复就绪即作为一条 ● vorto 消息（markdown）写进对话——
        # 回复**边生成边显示**：流式 token 进 #stream（署名 ● vorto、和最终消息同位同款，
        # 不再是早期那种"下方暗显再跳上去"的割裂感）；reply 就绪即清掉 #stream、把最终
        # markdown 写进 log（清在写之前，避免一帧双份 ● vorto）。工具调用的 JSON 不会流式（被 _complete 抑制）。
        stream = self.query_one("#stream", Static)

        def stream_cb(partial: str) -> None:
            self.query_one("#status", Static).display = False   # 有正文了，转圈让位
            stream.display = True
            t = Text()
            t.append("● ", style=f"bold {self._tc('text-success', '#7fce9a')}")  # 随主题，与最终回复同色
            t.append("vorto", style="dim italic")
            t.append("\n")
            t.append(partial[-1800:])        # 显示尾部，避免面板无限增高
            stream.update(t)

        def emit_final(text: str) -> None:
            stream.update(""); stream.display = False   # 先清流式区，再落最终（无双份）
            self._assistant(text)

        self._turn_tools = 0
        t0 = time.monotonic()
        reply = ""
        try:
            reply = await self.agent.run_turn(user_text, mode=self.mode, say=self._chrome,
                                              emit=emit_final, stream_cb=stream_cb,
                                              images=images, audio=audio)
        finally:
            stream.update(""); stream.display = False   # 出错/取消时也收干净
        if self._turn_tools:                # 用过工具的回合给个清晰收尾
            self._chrome(f"[dim]✓ 完成 · {self._turn_tools} 个工具 · {time.monotonic() - t0:.0f}s[/dim]")
        if self._speak_replies and reply:   # /speak 开：把这条回复合成语音朗读
            self._chrome("[dim]🔊 合成语音中…[/dim]")
            await self._speak_text(reply)
        self._persist_agent_history()

    def _persist_agent_history(self) -> None:
        """把 agent 当前上下文快照进会话，供 /resume 跨会话续上记忆。失败不影响交互。"""
        if not (self.agent and self.session_id and self._persist_on):
            return
        try:
            snap = json.dumps(self.agent.history[-40:], ensure_ascii=False)
            self.sessions.add_message("agent", snap, {"agent_history": True})
        except Exception:  # noqa: BLE001
            pass

    def _skill_registry(self, reload: bool = False):
        """技能注册表（惰性构建+缓存）。/skills reload 时 reload=True 重新扫描。"""
        from src.agents.main_agent import SkillRegistry
        if self._skills is None or reload:
            self._skills = SkillRegistry([
                str(Path(self.repo_root) / "skills"),
                str(Path(self.repo_root) / ".vortocode" / "skills"),
            ]).load()
        return self._skills

    def _build_main_agent(self):
        """构建主 agent 及其工具集（工具是闭包，复用本 TUI 已有的能力）。

        只读工具（read_file/list_files/analyze_repo）plan 也可用；写/重型工具
        （run_dev_workflow）仅 build —— 这就是 opencode Plan/Build 的"工具权限门"。
        """
        from src.agents.main_agent import MainAgent, Tool

        async def _t_read_file(args: dict) -> str:
            rel = str(args.get("path", "")).strip().lstrip("@")
            if not rel:
                return "缺少 path 参数。"
            try:
                return self._read_files([rel]) or f"(空文件或不存在: {rel})"
            except Exception as e:  # noqa: BLE001
                return f"读取失败: {e}"

        async def _t_list_files(args: dict) -> str:
            sub = str(args.get("dir", "")).strip().strip("/")
            files = _repo_files(self.repo_root)
            if sub:
                files = [f for f in files if f.startswith(sub)]
            return "\n".join(files[:200]) if files else "(没有匹配的源码文件)"

        async def _t_analyze_repo(args: dict) -> str:
            from src.orchestrator.self_analysis import analyze_self, render_report
            report = await analyze_self(self.repo_root)
            return render_report(report)

        async def _t_grep(args: dict) -> str:
            pat = str(args.get("pattern", "")).strip()
            if not pat:
                return "缺少 pattern 参数。"
            try:
                rx = re.compile(pat)
            except re.error as e:
                return f"无效正则: {e}"
            sub = str(args.get("dir", "")).strip().strip("/")
            files = _repo_files(self.repo_root)
            if sub:
                files = [f for f in files if f.startswith(sub)]
            hits: list[str] = []
            for f in files:
                try:
                    lines = (Path(self.repo_root) / f).read_text(encoding="utf-8", errors="ignore").splitlines()
                except Exception:  # noqa: BLE001
                    continue
                for i, line in enumerate(lines, 1):
                    if rx.search(line):
                        hits.append(f"{f}:{i}: {line.strip()[:200]}")
                        if len(hits) >= 100:
                            hits.append("…(命中过多，已截断)")
                            return "\n".join(hits)
            return "\n".join(hits) if hits else f"没有匹配 /{pat}/ 的内容。"

        def _safe_path(rel: str):
            """把相对路径锁在仓库内，防止 ../ 或绝对路径越界。返回 Path 或 None。"""
            base = Path(self.repo_root).resolve()
            try:
                p = (base / rel).resolve()
            except Exception:  # noqa: BLE001
                return None
            return p if (p == base or base in p.parents) else None

        async def _t_edit_file(args: dict) -> str:
            rel = str(args.get("path", "")).strip().lstrip("@")
            old, new = str(args.get("old", "")), str(args.get("new", ""))
            if not rel:
                return "缺少 path 参数。"
            if not old:
                return "edit_file 需要 old（要替换的原文）。"
            p = _safe_path(rel)
            if p is None:
                return f"路径越界或非法: {rel}"
            if not p.is_file():
                return f"文件不存在: {rel}"
            text = p.read_text(encoding="utf-8")
            cnt = text.count(old)
            if cnt == 0:
                return f"在 {rel} 中找不到要替换的原文（old）。"
            if cnt > 1:
                return f"原文在 {rel} 中出现 {cnt} 次、不唯一；请给更长、唯一的 old。"
            ok = await self._confirm_write(
                f"build 模式：修改 {rel}？替换 1 处（{len(old)}→{len(new)} 字符）。改动只进工作区，不碰 main。")
            if not ok:
                return f"用户取消了对 {rel} 的修改。"
            p.write_text(text.replace(old, new, 1), encoding="utf-8")
            self._show_diff(rel, old, new)        # 着色 diff 进对话区（仿 Claude Code）
            self._chrome(f"[green]已修改 {rel}（请 review；/diff 或 git diff 看全）[/green]")
            return f"已修改 {rel}（替换 1 处）。"

        async def _t_write_file(args: dict) -> str:
            rel = str(args.get("path", "")).strip().lstrip("@")
            content = str(args.get("content", ""))
            if not rel:
                return "缺少 path 参数。"
            p = _safe_path(rel)
            if p is None or p.is_dir():
                return f"路径越界或非法: {rel}"
            verb = "覆盖" if p.is_file() else "新建"
            ok = await self._confirm_write(
                f"build 模式：{verb}文件 {rel}（{len(content)} 字符）？改动只进工作区，不碰 main。")
            if not ok:
                return f"用户取消了写入 {rel}。"
            before = p.read_text(encoding="utf-8") if p.is_file() else ""
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            self._show_diff(rel, before, content)   # 着色 diff 进对话区
            self._chrome(f"[green]已{verb} {rel}（请 review；/diff 看全）[/green]")
            return f"已{verb} {rel}。"

        async def _t_run_dev(args: dict) -> str:
            goal = str(args.get("goal", "")).strip()
            if not goal:
                return "run_dev_workflow 需要 goal 参数（开发目标）。"
            return await self._run_dev(goal)

        async def _t_dev_isolated(args: dict) -> str:
            """隔离 worktree 里实现一步 + 在其中跑测试逐件验证，产出 diff（绿=可应用）待人工确认。"""
            desc = str(args.get("description") or args.get("task") or args.get("goal") or "").strip()
            if not desc:
                return "dev_isolated 需要 description（要在隔离工作区实现的任务）。"
            import sys
            import uuid
            from src.agents.worktree import run_isolated_task
            from src.agents.main_agent import build_read_tools, build_write_tools
            wid = "wt-" + uuid.uuid4().hex[:8]
            sel = str(args.get("test") or "").strip()                 # 可选：narrow 到某些测试
            test_cmd = [sys.executable, "-m", "pytest", "-q", sel or "tests/"]
            self._chrome(f"[magenta]🧪 隔离实现：{desc}[/magenta][dim]（独立 worktree，完成后跑测试验证）[/dim]")

            def _build(wt_path: str):
                from src.agents.main_agent import build_test_tool
                return MainAgent(
                    build_read_tools(wt_path) + build_write_tools(wt_path)
                    + [build_test_tool(wt_path, test_cmd)],
                    max_steps=16, on_tool=self._audit_tool,
                    extra_system=("你是隔离工作区里的实现子 agent：用 read_file/list_files/grep 看代码，"
                                  "用 edit_file/write_file 实现任务；改完务必用 run_tests 自测，没过就读失败、"
                                  "改、再测，直到通过再结束。完成后一两句说明改了什么。只动与任务相关的文件。"))
            try:
                diff, conclusion, ver = await run_isolated_task(
                    self.repo_root, wid, desc, _build, test_cmd=test_cmd)
            except Exception as e:  # noqa: BLE001
                return f"(隔离实现出错: {e})"
            if not (diff or "").strip():
                return f"子 agent 没产生任何改动。结论：{conclusion}"
            self._chrome("[b]🧪 隔离工作区改动（未并入主工作区）[/b]")
            self._render_diff_text(diff)
            nlines = diff.count("\n")
            tail = (ver or {}).get("output", "")[-1200:]
            cmd = (ver or {}).get("cmd", "")
            if ver and ver["ok"]:
                self._chrome(f"[{self._tc('text-success', '#7fce9a')}]✅ 测试通过[/]"
                             f"[dim]（{cmd}）[/dim]")
                import re
                slug = re.sub(r"[^a-z0-9]+", "-", desc.lower()).strip("-")[:28] or "iso"
                branch = f"vorto/{slug}-{wid[3:]}"
                applied = ""
                if await self._confirm_write(
                        f"测试已过。把这块改动应用到新分支 {branch}？（不碰 main / 当前工作区）"):
                    import asyncio
                    from src.agents.worktree import apply_diff_to_branch
                    res = await asyncio.to_thread(
                        apply_diff_to_branch, self.repo_root, branch, diff, f"dev_isolated: {desc}")
                    if res["ok"]:
                        self._chrome(f"[{self._tc('text-success', '#7fce9a')}]✅ 已应用到分支 "
                                     f"[b]{branch}[/b]（git checkout {branch} 查看，仍未碰 main）[/]")
                        applied = f"，并已应用到新分支 {branch}"
                    else:
                        self._chrome(f"[{self._tc('text-error', '#f08a8a')}]应用到分支失败：{res['error']}[/]")
                        applied = f"，但应用到分支失败：{res['error']}"
                else:
                    applied = "（未应用，diff 仅展示）"
                return (f"✅ 隔离实现完成且测试通过（{cmd}）。diff {nlines} 行{applied}。结论：{conclusion}")
            self._chrome(f"[{self._tc('text-error', '#f08a8a')}]❌ 测试未过[/]"
                         f"[dim]（{cmd}）—— 这块先别并入。[/dim]")
            if tail:
                self._chrome(f"[dim]{tail.replace('[', chr(92) + '[')}[/dim]")   # 转义 [ 防当成标记
            return (f"❌ 隔离实现完成但测试未过（{cmd}）。失败输出尾部：\n{tail}\n"
                    f"请据此修正后重试（再调 dev_isolated）。diff {nlines} 行，未并入。结论：{conclusion}")

        async def _t_dev_parallel(args: dict) -> str:
            """并行实现：多个相互独立的子任务各起一个隔离 worktree 同时实现+验证（互不冲突），
            汇总各自 ✅/❌；通过的可一并应用到一个新分支待人工确认。"""
            tasks = args.get("tasks") or args.get("descriptions") or []
            if isinstance(tasks, str):
                tasks = [tasks]
            tasks = [str(t).strip() for t in tasks if str(t).strip()][:5]   # 最多 5，防失控
            if not tasks:
                return "dev_parallel 需要 tasks（相互独立的子任务字符串列表）。"
            import asyncio
            import sys
            import uuid
            from src.agents.worktree import run_isolated_task, apply_diffs_to_branch
            from src.agents.main_agent import build_read_tools, build_write_tools
            sel = str(args.get("test") or "").strip()
            test_cmd = [sys.executable, "-m", "pytest", "-q", sel or "tests/"]
            self._chrome(f"[magenta]🧪 并行隔离实现（{len(tasks)}）—— 各自独立 worktree、互不冲突[/magenta]")

            async def _one(desc):
                wid = "wt-" + uuid.uuid4().hex[:8]

                def _b(wt):
                    from src.agents.main_agent import build_test_tool
                    return MainAgent(
                        build_read_tools(wt) + build_write_tools(wt) + [build_test_tool(wt, test_cmd)],
                        max_steps=16, on_tool=self._audit_tool,
                        extra_system=("你是隔离工作区里的实现子 agent：用 read/grep 看代码、用 edit_file/"
                                      "write_file 实现任务；改完务必用 run_tests 自测，没过就改完再测直到通过。"
                                      "完成后一两句说明改了什么。只动相关文件。"))
                try:
                    diff, conclusion, ver = await run_isolated_task(self.repo_root, wid, desc, _b, test_cmd=test_cmd)
                    return {"desc": desc, "diff": diff, "conclusion": conclusion, "ver": ver, "error": None}
                except Exception as e:  # noqa: BLE001
                    return {"desc": desc, "diff": "", "conclusion": "", "ver": None, "error": str(e)}

            results = await asyncio.gather(*[_one(t) for t in tasks])

            greens = []
            for r in results:
                head = f"[b]· {r['desc']}[/b]"
                if r["error"]:
                    self._chrome(f"{head} [{self._tc('text-error', '#f08a8a')}](出错: {r['error']})[/]")
                elif not (r["diff"] or "").strip():
                    self._chrome(f"{head} [dim](无改动)[/dim]")
                elif r["ver"] and r["ver"]["ok"]:
                    self._chrome(f"{head} [{self._tc('text-success', '#7fce9a')}]✅ 通过[/]"
                                 f"[dim]（{r['diff'].count(chr(10))} 行）[/dim]")
                    greens.append(r)
                else:
                    self._chrome(f"{head} [{self._tc('text-error', '#f08a8a')}]❌ 未过[/]"
                                 f"[dim]（{r['diff'].count(chr(10))} 行）[/dim]")

            if not greens:
                return f"并行 {len(tasks)} 个子任务完成；没有通过测试、可应用的改动。"
            branch = "vorto/parallel-" + uuid.uuid4().hex[:8]
            note = "（未应用，diff 仅展示）"
            if await self._confirm_write(
                    f"{len(greens)} 个子任务测试通过。把它们都应用到新分支 {branch}？（不碰 main / 当前工作区）"):
                items = [(g["diff"], f"dev_parallel: {g['desc']}") for g in greens]
                res = await asyncio.to_thread(apply_diffs_to_branch, self.repo_root, branch, items)
                if res["applied"]:
                    self._chrome(f"[{self._tc('text-success', '#7fce9a')}]✅ 已应用 {len(res['applied'])} 块到分支 "
                                 f"[b]{branch}[/b]（git checkout {branch} 查看）[/]")
                    note = f"，{len(res['applied'])}/{len(greens)} 块已应用到 {branch}"
                if res["failed"]:
                    self._chrome(f"[{self._tc('text-warning', '#f0b86e')}]{len(res['failed'])} 块未能干净应用"
                                 f"（可能互相冲突），已跳过[/]")
            return f"并行 {len(tasks)} 个子任务：{len(greens)} 通过测试{note}。"

        async def _t_open_pr(args: dict) -> str:
            """把一个本地分支（如 dev_isolated 产出的 vorto/...）push 上去并开 PR。外向操作，强确认。"""
            branch = str(args.get("branch", "")).strip()
            title = str(args.get("title", "")).strip()
            body = str(args.get("body", "")).strip()
            if not branch or not title:
                return "open_pr 需要 branch 和 title。"
            if not await self._confirm_outward(
                    f"把分支 {branch} push 到 origin 并开 PR「{title}」？这是外向操作（推到远端、建 PR）。"):
                return f"用户取消了为 {branch} 开 PR。"
            import asyncio
            from src.agents.vcs import push_and_open_pr
            res = await asyncio.to_thread(push_and_open_pr, self.repo_root, branch, title, body)
            if res["ok"]:
                self._chrome(f"[{self._tc('text-success', '#7fce9a')}]✅ 已开 PR：{res['url']}[/]")
                return f"已 push {branch} 并开 PR：{res['url']}"
            if res.get("pushed"):
                self._chrome(f"[{self._tc('text-warning', '#f0b86e')}]已 push {branch}，但开 PR 失败：{res['error']}[/]")
                return f"已 push {branch}，但开 PR 失败：{res['error']}（可手动 gh pr create）"
            self._chrome(f"[{self._tc('text-error', '#f08a8a')}]开 PR 失败：{res['error']}[/]")
            return f"开 PR 失败：{res['error']}"

        async def _t_run_command(args: dict) -> str:
            """跑任意 shell 命令（测试/lint/git/构建…）。高危：build 门控 + 人工确认 + 危险拦截。"""
            cmd = str(args.get("command") or args.get("cmd") or "").strip()
            if not cmd:
                return "run_command 需要 command。"
            from src.agents.shell import is_dangerous, run_command
            why = is_dangerous(cmd)
            if why:                                   # 兜底硬拒（即便始终允许）
                self._chrome(f"[{self._tc('text-error', '#f08a8a')}]拒绝执行（{why}）：{cmd}[/]")
                return f"拒绝执行（疑似危险操作：{why}）。请换更具体、安全的命令。"
            if not await self._confirm_write(
                    f"build 模式：在仓库根目录执行命令？\n  $ {cmd}\n（可能改动工作区，但不碰 main）"):
                return f"用户取消了命令：{cmd}"
            self._chrome(f"[dim]$ {cmd}[/dim]")
            import asyncio
            res = await asyncio.to_thread(run_command, self.repo_root, cmd)
            out = res["output"]
            if out.strip():
                self._chrome(f"[dim]{out[-1500:].replace('[', chr(92) + '[')}[/dim]")
            ok_c = self._tc("text-success", "#7fce9a") if res["ok"] else self._tc("text-error", "#f08a8a")
            self._chrome(f"[{ok_c}]{'✓' if res['ok'] else '✗'} exit {res['code']}[/]")
            return f"命令 `{cmd}` 退出码 {res['code']}。输出尾部：\n{out[-3000:]}"

        # 只读工具：plan 也能用；也是子 agent 的工具集（无 task/写工具 → 不嵌套、不改文件）
        read_tools = [
            Tool("read_file", "读取仓库内某个文件的内容",
                 {"path": "相对路径，如 src/cli.py"}, _t_read_file, read_only=True),
            Tool("list_files", "列出仓库内的源码文件（可按子目录前缀过滤）",
                 {"dir": "可选，子目录前缀，如 src/tui"}, _t_list_files, read_only=True),
            Tool("grep", "在仓库源码里按正则搜索，返回 path:line: 命中行",
                 {"pattern": "正则表达式", "dir": "可选，子目录前缀"}, _t_grep, read_only=True),
            Tool("analyze_repo", "只读扫描本仓库，列出问题清单（孤儿模块/循环依赖/测试缺口等），无需 key",
                 {}, _t_analyze_repo, read_only=True),
        ]

        async def _spawn_research(desc: str) -> str:
            """起一个隔离的只读子 agent 做调研，返回结论。task 与 research_parallel 共用。"""
            sub = MainAgent(read_tools, max_steps=12, on_tool=self._audit_tool, extra_system=(
                "你是只读研究子 agent：只用工具调研代码/仓库并返回简洁结论，绝不修改任何东西。"
                "读够信息就尽快收口，别把预算耗在重复读取上。"))
            try:
                r = await sub.run_turn(desc, mode=self.mode, say=self._chrome, emit=lambda _t: None)
            except Exception as e:  # noqa: BLE001
                return f"(子任务出错: {e})"
            return r or "(无结论)"

        def _preview(s: str, n: int = 200) -> str:
            s = s.replace("\n", " ")
            return s[:n] + ("…" if len(s) > n else "")

        async def _t_task(args: dict) -> str:
            desc = str(args.get("description") or args.get("task") or "").strip()
            if not desc:
                return "task 需要 description（要委派给子 agent 的研究任务）。"
            self._chrome(f"[magenta]🤖 子 agent 研究：{desc}[/magenta]")
            result = await _spawn_research(desc)
            self._chrome(f"[dim]  ↳ 结论：{_preview(result)}[/dim]")   # 子 agent 结论可见
            return result

        async def _t_research_parallel(args: dict) -> str:
            tasks = args.get("tasks") or args.get("descriptions") or []
            if isinstance(tasks, str):
                tasks = [tasks]
            tasks = [str(t).strip() for t in tasks if str(t).strip()][:5]
            if not tasks:
                return "research_parallel 需要 tasks（字符串列表，每项一个独立子问题）。"
            import asyncio
            self._chrome(f"[magenta]🤖 并行子 agent（{len(tasks)}）研究中…[/magenta]")
            results = await asyncio.gather(*[_spawn_research(t) for t in tasks])
            for t, r in zip(tasks, results):     # 各路结论都可见
                self._chrome(f"[dim]  ↳ [{_preview(t, 30)}] {_preview(r, 160)}[/dim]")
            return "\n\n".join(f"【{t}】\n{r}" for t, r in zip(tasks, results))

        # SKILL.md 技能：按需加载（progressive disclosure），复用 src.skills 的解析器
        registry = self._skill_registry()

        async def _t_use_skill(args: dict) -> str:
            name = str(args.get("name") or args.get("skill") or "").strip()
            sk = registry.get(name)
            if not sk:
                avail = "、".join(registry.skills) or "（无）"
                return f"没有名为 {name} 的技能。可用：{avail}"
            return (f"【技能「{sk.name}」完整指令】请据此执行（用你的其它工具完成），"
                    f"不要原样复述给用户：\n\n{sk.instructions}")

        async def _t_save_skill(args: dict) -> str:
            name = re.sub(r"[^\w一-鿿-]", "-", str(args.get("name", "")).strip()).strip("-")
            desc = str(args.get("description", "")).strip()
            instr = str(args.get("instructions", "")).strip()
            if not name or not instr:
                return "save_skill 需要 name 和 instructions（技能正文）。"
            p = Path(self.repo_root) / ".vortocode" / "skills" / name / "SKILL.md"
            ok = await self._confirm_write(
                f"build 模式：把技能「{name}」写到 .vortocode/skills/{name}/SKILL.md？（用户技能目录，不碰 main）")
            if not ok:
                return f"用户取消了保存技能 {name}。"
            content = f"---\nname: {name}\ndescription: {desc}\n---\n\n{instr}\n"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            self._skill_registry(reload=True)   # 原地重扫：当前 agent 立刻能 use_skill 到它
            self._chrome(f"[green]已保存技能 {name}（/skills 可见）[/green]")
            return f"已保存技能 {name} 到 .vortocode/skills/{name}/SKILL.md。"

        # 跨会话长期记忆（固定 __longterm__ session_id，复用 SessionStore 的 memories 表）
        async def _t_save_memory(args: dict) -> str:
            content = str(args.get("content", "")).strip()
            if not content:
                return "save_memory 需要 content（要长期记住的事实/偏好/约定）。"
            try:
                self.sessions.store.add_memory("__longterm__", "fact", content, importance=0.6)
            except Exception as e:  # noqa: BLE001
                return f"保存记忆失败: {e}"
            return f"已记住（跨会话）：{content[:80]}"

        async def _t_recall_memory(args: dict) -> str:
            q = str(args.get("query", "")).strip()
            try:
                rows = (self.sessions.store.search_memories("__longterm__", q, 10) if q
                        else self.sessions.store.get_memories("__longterm__"))
            except Exception as e:  # noqa: BLE001
                return f"检索记忆失败: {e}"
            if not rows:
                return "（没有相关的长期记忆）"
            return "相关长期记忆:\n" + "\n".join(f"- {r['content']}" for r in rows[:10])

        tools = read_tools + [
            Tool("task", "把一个独立的研究/调研子任务委派给只读子 agent（隔离上下文），返回它的结论",
                 {"description": "要委派的子任务"}, _t_task, read_only=True),
            Tool("save_memory", "把一条要跨会话长期记住的事实/偏好/约定存起来",
                 {"content": "要记住的内容"}, _t_save_memory, read_only=True),
            Tool("recall_memory", "检索跨会话长期记忆（不传 query 则列出全部）",
                 {"query": "可选，关键词"}, _t_recall_memory, read_only=True),
            Tool("research_parallel", "并行委派多个只读子 agent 同时研究不同子问题，汇总各自结论（最多 5 个）",
                 {"tasks": "子问题字符串列表"}, _t_research_parallel, read_only=True),
            Tool("use_skill", "加载某个技能(SKILL.md)的完整指令到上下文，然后据此执行",
                 {"name": "技能名"}, _t_use_skill, read_only=True),
            Tool("save_skill", "把一套可复用流程保存成新技能(SKILL.md)到用户技能目录；写操作，需确认，仅 build",
                 {"name": "技能名", "description": "一句话描述", "instructions": "技能正文（自然语言步骤）"},
                 _t_save_skill, read_only=False),
            Tool("edit_file", "对仓库文件做精确字符串替换（old 必须唯一存在）；写操作，需确认，仅 build",
                 {"path": "相对路径", "old": "要替换的原文(需唯一)", "new": "替换为"},
                 _t_edit_file, read_only=False),
            Tool("write_file", "新建或覆盖仓库文件；写操作，需确认，仅 build",
                 {"path": "相对路径", "content": "文件全部内容"}, _t_write_file, read_only=False),
            Tool("run_dev_workflow",
                 "把一个明确的开发目标交给 dev→test→review 流水线自动实现+测试（重型，仅 build 模式）",
                 {"goal": "开发目标（自然语言）"}, _t_run_dev, read_only=False),
            Tool("dev_isolated",
                 "在隔离 git worktree 里让可写子 agent 实现一个独立子任务，并在其中跑测试逐件验证，"
                 "产出 diff（✅通过=可应用 / ❌未过=带失败输出供修正）待人工确认；绝不碰主工作区。"
                 "大任务可对计划里相互独立的步骤逐个调它（仅 build）",
                 {"description": "要在隔离工作区实现的子任务",
                  "test": "可选，pytest 选择器(如 tests/unit/test_x.py)，省略则跑全量 tests/"},
                 _t_dev_isolated, read_only=False),
            Tool("dev_parallel",
                 "并行实现：多个**相互独立**的子任务各起一个隔离 worktree 同时实现+验证（互不冲突），"
                 "汇总各自 ✅/❌；通过的可一并应用到一个新分支待确认。把计划里独立的步骤一次交给它"
                 "（最多 5 个，仅 build）",
                 {"tasks": "相互独立的子任务字符串列表",
                  "test": "可选，pytest 选择器，省略则各自跑全量 tests/"},
                 _t_dev_parallel, read_only=False),
            Tool("open_pr",
                 "把一个本地分支（如 dev_isolated/dev_parallel 产出的 vorto/...）push 到 origin 并开 PR；"
                 "外向操作、强确认，gh 不可用则只 push（仅 build）",
                 {"branch": "要开 PR 的分支名", "title": "PR 标题", "body": "可选，PR 正文"},
                 _t_open_pr, read_only=False),
            Tool("run_command",
                 "在仓库根目录跑任意 shell 命令（如 pytest 某个文件 / ruff / git log / pip install / make）；"
                 "高危，每条都需确认、明显危险操作直接拒（仅 build）",
                 {"command": "要执行的 shell 命令"}, _t_run_command, read_only=False),
        ]

        # 制品（artifact）：把会话产出发布成可分享、实时更新的网页（由 Web 服务器在 /artifact 渲染）。
        # 首次发布弹确认（对齐 CC「批准后再发不再问」：更新静默），发布成功提示可点链接。
        from src.web.artifacts import build_artifact_tools

        async def _artifact_confirm(preview: dict, _is_update: bool) -> bool:
            t = preview.get("title") or preview.get("id") or "未命名"
            return await self._confirm_write(
                f"build 模式：把制品「{t}」发布成网页？"
                f"（存到 .vortocode/artifacts/，Web 服务器在 /artifact/<id> 渲染、可分享）")

        def _artifact_published(meta: dict, url: str) -> None:
            self._chrome(f"[green]制品已发布 v{meta['version']}：{url}（/artifacts 看全部）[/green]")

        async def _artifact_confirm_delete(preview: dict) -> bool:
            return await self._confirm_write(
                f"build 模式：删除制品 {preview.get('id')}？此操作不可撤销。")

        tools += build_artifact_tools(self.repo_root, confirm=_artifact_confirm,
                                      on_published=_artifact_published,
                                      confirm_delete=_artifact_confirm_delete)
        tools += self._mcp_tools             # 已接入的外部 MCP 工具（build 门控）
        catalog = registry.catalog()
        extra = f"【可用技能】(需要时用 use_skill 加载其完整指令再执行)\n{catalog}" if catalog else None
        import os
        native = os.getenv("VORTOCODE_NATIVE_TOOLS", "").lower() in ("1", "true", "yes", "on")
        hook_system = self._load_hook_system()   # .vortocode/hooks.yaml 存在才接，避免无谓开销
        return MainAgent(tools, extra_system=extra, native=native,
                         on_tool=self._audit_tool, on_escalate=self._escalate_to_build,
                         on_plan=self._render_plan, plan_tool=True, hook_system=hook_system)

    def _load_hook_system(self):
        """有 .vortocode/hooks.yaml 才建 HookSystem（复用 src/hooks，把工具生命周期事件接进 agent）。"""
        cfg = Path(self.repo_root) / ".vortocode" / "hooks.yaml"
        if not cfg.is_file():
            return None
        try:
            from src.hooks import HookSystem
            return HookSystem(config_path=str(cfg))
        except Exception as e:  # noqa: BLE001
            self._chrome(f"[dim]（hooks.yaml 加载失败，已忽略：{e}）[/dim]")
            return None

    def _render_plan(self, plan: list) -> None:
        """把主 agent 的任务清单渲染成一块带进度的可见面板（每次更新重渲，看着它推进）。"""
        from src.agents.plan import plan_progress
        done, total = plan_progress(plan)
        styles = {"completed": ("✓", self._tc("text-success", "#7fce9a")),
                  "in_progress": ("▸", self._tc("text-warning", "#f0b86e")),
                  "pending": ("○", "dim")}
        lines = [f"[b]📋 计划 · {done}/{total}[/b]"]
        for p in plan:
            glyph, color = styles.get(p.get("status"), ("○", "dim"))
            step = p["step"].replace("[", r"\[")       # 防步骤文本里的方括号被当成标记
            lines.append(f"  [{color}]{glyph}[/] {step}")
        self._chrome("\n".join(lines))

    async def _escalate_to_build(self, name: str, args: dict) -> bool:
        """plan 模式下主 agent 想用写/重型工具时：问用户切不切 build，同意则切并继续。

        本会话已"始终允许"则直接切（不再问）。切了之后整个会话留在 build。
        """
        if self._allow_writes_session:
            ok = True
        else:
            ok = await self.push_screen_wait(ConfirmScreen(
                f"plan(只读)模式下，这一步要用写/重型工具「{name}」。切到 build 模式并继续？"))
        if ok and self.mode != "build":
            self.mode = "build"
            self._sync_subtitle()
            self._chrome("[green]→ 已切到 build 模式并继续[/green]")
        return ok

    def _audit_tool(self, name: str, args: dict, result: str) -> None:
        """把一次工具调用写进审计日志（.vortocode/audit.log，JSONL）。失败不影响交互。"""
        from datetime import datetime
        self._turn_tools += 1               # 本回合工具计数（回合结束反馈用）
        try:
            p = Path(self.repo_root) / ".vortocode" / "audit.log"
            p.parent.mkdir(parents=True, exist_ok=True)
            rec = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "mode": self.mode,
                "tool": name,
                "args": {k: str(v)[:120] for k, v in (args or {}).items()},
                "result_len": len(str(result)),
            }
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            pass
        try:
            self._tool_preview(result)        # 工具结果摘要行（仿 Claude Code 的 ⎿）
        except Exception:  # noqa: BLE001
            pass

    def _tool_preview(self, result: str) -> None:
        """在工具调用(🔧)行下方补一条结果摘要：⎿ 首行 (+N 行)，按字面、不入 transcript。"""
        lines = str(result or "").strip().splitlines()
        if not lines:
            return
        t = Text("  ⎿ ", style="dim")
        t.append(lines[0][:120], style="dim")
        if len(lines) > 1:
            t.append(f"  (+{len(lines) - 1} 行)", style="dim")
        self.query_one("#log", RichLog).write(t)

    def _render_diff_text(self, diff_text: str, max_lines: int = 200) -> None:
        """把 unified diff 着色渲染到对话区：+绿 / -红 / @@蓝 / 文件头 dim（随主题，仿 Claude Code）。"""
        add = self._tc("text-success", "#7fce9a")
        rem = self._tc("text-error", "#f08a8a")
        hunk = self._tc("text-accent", "#8ab4f8")
        lines = diff_text.splitlines()
        log = self.query_one("#log", RichLog)
        for line in lines[:max_lines]:
            if line.startswith("+") and not line.startswith("+++"):
                style = add
            elif line.startswith("-") and not line.startswith("---"):
                style = rem
            elif line.startswith("@@"):
                style = hunk
            elif line.startswith(("+++", "---", "diff ", "index ", "new file", "deleted")):
                style = "bold dim"
            else:
                style = "dim"
            log.write(Text(line, style=style))
        if len(lines) > max_lines:
            log.write(Text(f"  … (+{len(lines) - max_lines} 行 diff，/diff 或 git diff 看全)", style="dim"))

    def _show_diff(self, rel: str, old_text: str, new_text: str, max_lines: int = 40) -> None:
        """渲染一次编辑的 unified diff（old→new）；无变化则不显示。失败不影响写入。"""
        try:
            import difflib
            diff = "\n".join(difflib.unified_diff(
                old_text.splitlines(), new_text.splitlines(),
                fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm=""))
            if diff.strip():
                self._render_diff_text(diff, max_lines)
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- 开发流水线
    @work(exclusive=True, group="action")
    async def _do_run(self, goal: str) -> None:
        """/run 入口：直接进开发流水线（不经意图判定）。"""
        await self._run_dev(goal)

    def _safe_repo_path(self, rel: str):
        """把相对路径锁在仓库内，挡 ../ 与绝对路径越界。返回 Path 或 None。"""
        base = Path(self.repo_root).resolve()
        try:
            p = (base / rel).resolve()
        except Exception:  # noqa: BLE001
            return None
        return p if (p == base or base in p.parents) else None

    @work(exclusive=True, group="action")
    async def _do_apply(self) -> None:
        """/apply：把最近一次 dev 流水线产出（工作区代码）带 diff+确认地落到仓库，闭合"实现→应用"。"""
        last = self._last_dev
        if not last or not last.get("files"):
            self._emit("(没有待应用的产出。先 /run <目标> 或让主 agent 开发，跑出产出后再 /apply。)")
            return
        ws = Path(last["workspace"])
        self._chrome(f"[cyan]待应用产出（{len(last['files'])} 个文件，逐个看 diff）:[/cyan]")
        pending = []                          # (rel, dst, new)
        for rel in last["files"]:
            src = ws / rel
            if not src.is_file():
                continue
            dst = self._safe_repo_path(rel)
            if dst is None:
                self._emit(f"(跳过越界路径: {rel})")
                continue
            try:
                new = src.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            old = dst.read_text(encoding="utf-8") if dst.is_file() else ""
            if old == new:
                continue                      # 与仓库一致，无需应用
            self._chrome(f"[b]{rel}[/b] [dim]({'修改' if old else '新建'})[/dim]")
            self._show_diff(rel, old, new)
            pending.append((rel, dst, new))
        if not pending:
            self._emit("(工作区产出与仓库一致，无需应用。)")
            return
        ok = await self._confirm_write(
            f"把这 {len(pending)} 个文件从工作区应用到仓库？"
            f"（只改工作区、不碰 main；/diff 可复核、git 可回滚）")
        if not ok:
            self._emit("已取消应用。")
            return
        applied = []
        for rel, dst, new in pending:
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(new, encoding="utf-8")
                applied.append(rel)
            except Exception as e:  # noqa: BLE001
                self._emit(f"写 {rel} 失败: {e}")
        self._chrome(f"[green]已应用 {len(applied)} 个文件到仓库（/diff 复核，git diff 可查）[/green]")
        self._last_dev = None

    async def _run_dev(self, goal: str) -> str:
        """跑 dev→test→review 流水线。返回一句结果摘要（供主 agent 回灌/汇总）。"""
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
            summary = f"结果: {ok} · 迭代 {result.iterations} · 工作区 {result.workspace}"
            self._emit(summary)
            if result.files:
                files_line = "产出文件: " + ", ".join(result.files)
                self._emit(files_line)
                summary += " · " + files_line
                # 记下产出，供 /apply 把工作区代码（带 diff+确认）落到仓库 —— 闭合"实现→应用"流程
                self._last_dev = {"workspace": result.workspace, "files": list(result.files)}
                self._chrome("[cyan]产出在工作区。用 [b]/apply[/b] 把它（先给 diff、再确认）应用到仓库。[/cyan]")
        except Exception as e:  # noqa: BLE001
            summary = f"执行出错: {e}"
            self._emit(summary)
        finally:
            stream.update("")
            stream.display = False
        return summary


def run() -> None:
    """启动 TUI（供 CLI 调用）。"""
    VortoCodeTUI(repo_root=".").run()
