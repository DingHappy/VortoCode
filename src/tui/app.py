"""VortoCode 交互式全屏 TUI（仿 opencode 的形）。

一个 Textual 应用：上方对话区、下方输入区、底部状态栏。
- 自然语言 = 开发目标（等同 /run）；slash 命令驱动各能力。
- `@文件` 在输入时幽灵文本补全，运行时把文件内容带入上下文。
- token 级流式：开发过程（developer 写代码）边生成边显示。
- plan / build 模式（Tab）：plan=只读/只出提案；build=允许写新分支（绝不碰 main）。

需要 textual（`pip install '.[tui]'`）。无 LLM key 时，需要模型的命令优雅降级、不崩。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from rich.markdown import Markdown
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Header, Input, RichLog, Static, TextArea
from textual.worker import WorkerState

from src.memory.session_store import SessionManager

SLASH_COMMANDS = [
    "/analyze", "/improve", "/fix", "/run", "/apply", "/agents", "/runagent", "/skills", "/mcp",
    "/artifacts", "/diff", "/changes", "/review", "/verify", "/preflight", "/git", "/commit", "/pr", "/pr-check", "/pr-fix", "/sessions", "/resume", "/new", "/mode", "/plan", "/build", "/model", "/think", "/theme", "/usage",
    "/context", "/compact", "/permissions", "/memory", "/tasks", "/tools", "/audit", "/speak", "/commands", "/hooks", "/clear", "/help", "/quit",
]
# 必须带参数的命令：补全面板里回车不直接执行，先补成 "/cmd " 让用户接着填参数
ARG_SLASH_CMDS = {"/fix", "/run", "/resume", "/runagent", "/pr-check", "/pr-fix"}
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
    "/diff": "看工作区改动（支持 stat/cached/路径过滤）",
    "/changes": "提交前变更审查摘要（风险信号/下一步）",
    "/review": "LLM 审查当前 diff/hunk（只报 P0/P1；--fix 可确认后修复）",
    "/verify": "探测测试或运行 profile/runtime 验证",
    "/preflight": "提交/开 PR 前检查（风险/审查/测试/提交建议）",
    "/git": "查看 git 状态、staged/unstaged diffstat",
    "/commit": "提交已 staged 改动；suggest 自动生成提交信息",
    "/pr": "预览或创建 PR；preview 只预览，draft 开草稿",
    "/pr-check": "读取 PR review 评论和失败 CI 检查",
    "/pr-fix": "确认后按 PR 反馈切 build 并调用 pr_fix 修复",
    "/sessions": "列出/恢复/重命名/删除历史会话",
    "/resume": "恢复某个历史会话",
    "/new": "新开一个会话",
    "/mode": "切换 plan / build 模式",
    "/plan": "进入 plan 权限模式（只读/提案）",
    "/build": "进入 build 权限模式（可写分支）",
    "/model": "查看/切换模型（/model 名称，本会话生效）",
    "/think": "开关「思考呈现」（推理型模型的思维链 dim 显示）",
    "/theme": "切换配色主题（21 套内置，记住选择）",
    "/usage": "本会话 token 用量（reset 清零）",
    "/context": "查看/切换上下文策略（auto/compact/balanced/preserve）",
    "/compact": "手动压缩旧对话上下文；preview 只预估",
    "/permissions": "查看/解释工具权限；可 deny 规则或 reset 会话放行",
    "/memory": "查看/管理项目指令和跨会话记忆",
    "/tasks": "列出/查看/续跑 dev_auto 持久化计划",
    "/tools": "列出主 agent 工具及读写权限",
    "/audit": "查看工具调用审计日志",
    "/speak": "朗读 agent 回复开关（mimo-v2.5-tts，需 key）",
    "/commands": "列出 .vortocode/commands 自定义命令（reload 重扫）",
    "/hooks": "列出 .vortocode/hooks.yaml 工具生命周期钩子",
    "/clear": "清屏",
    "/help": "显示帮助",
    "/quit": "退出",
}
ACTION_CMDS = {"analyze", "improve", "fix", "run", "apply", "runagent", "mcp", "verify"}   # 跑长任务，受忙碌态约束

# 工作中指示器（仿 Claude Code）：10 帧 braille 旋转 + 轮换动词 + 计时 + esc 中断
_SPIN_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPIN_VERBS = ["思考中", "琢磨中", "检索中", "运转中", "推敲中"]


def _model_name() -> str:
    """状态栏显示的模型名：直接取 LLMConfig().model（与客户端同源）。

    必须经 LLMConfig 取、别只读 os.getenv——加载 .env（设 DEFAULT_MODEL）的是 src.llm.client 的
    模块级 load_dotenv。若此函数在 client 导入前就读 env，会读不到 DEFAULT_MODEL 而错误回退成
    gpt-4o-mini（状态栏一进来就显示错的）。import LLMConfig 会确保 .env 已加载，得到真实模型。
    """
    try:
        from src.llm.client import LLMConfig
        return LLMConfig().model
    except Exception:  # noqa: BLE001 —— 兜底：拿不到就退回 env 取法
        import os
        return os.getenv("DEFAULT_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"

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
    """剔除漏进输入的终端转义/控制序列，返回干净文本（普通可见字符 + 空格/Tab/换行）。

    换行是多行编辑器（PromptEditor，Ctrl+J）的合法内容，必须放行；其余控制字符照剔。
    """
    s = _CTRL_SEQ_RE.sub("", s)
    return "".join(ch for ch in s if ch >= " " or ch in ("\t", "\n"))

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
  /diff [stat|hunks|cached] [路径]  看工作区改动；hunks 显示可 review 的 hunk 编号
  /changes [cached] [路径]  提交前变更审查摘要（风险信号/下一步）
  /review [--fix] [hunk H1] [cached] [路径]  LLM 审查当前 diff/hunk；--fix 确认后切 build 修复
  /verify [selector|--changed]  探测并运行仓库测试；--changed 按改动推断相关测试
  /verify profiles     列出 verify profile；/verify <profile> 运行 profile
  /verify run <命令>   运行 runtime/smoke 验证命令（危险拦截 + 人工确认）
  /preflight [cached] 提交/开 PR 前检查：风险、建议审查、建议验证、建议提交信息
  /git                查看 git 状态、staged/unstaged diffstat
  /commit <msg|suggest> 提交已 staged 改动；/commit all --suggest 先 git add -A 并自动生成信息
  /pr [preview|draft] [base <ref>] [title]  预览或创建 PR（外向操作需确认）
  /pr-check <ref>     读取 PR review 评论和失败 CI 检查
  /pr-fix <ref>       确认后切 build 并让主 agent 调 pr_fix 修复 PR 反馈
  /mcp [list|off]     接入 config/mcp.yaml 的 MCP 服务器工具（build 门控）
  /agents             列出已创建的 agent（网页/API 建的，同一份存储）
  /runagent <id> <任务>  用某个已创建的 agent 执行任务（流式）
  /sessions [操作]    列出历史会话；rename/delete 管理会话
  /resume <id>        恢复某个历史会话
  /new                新开一个会话
  /mode               切换 plan(只读/提案) / build(可写分支)
  /plan               进入 plan 权限模式
  /build              进入 build 权限模式
  /theme [名]         切换配色主题（不带名=列出全部；选择会记住，下次自动用）
  /usage [reset]      本会话 token 用量（估算；reset 清零）
  /context [策略]     查看/切换上下文策略（auto/compact/balanced/preserve）
  /compact [preview]  手动压缩旧对话上下文；preview 只预估
  /permissions [操作] 查看/解释工具权限；explain <tool> [value]，deny <tool> [glob] 加规则
  /memory [操作]      查看/管理长期记忆；list/delete/auto/add/init
  /tasks [show|resume <id>]  列出、查看或续跑 dev_auto 持久化计划
  /clear              清屏
  /help               显示本帮助
  /quit               退出（也可 Ctrl+C）
键位: Tab=补全/切模式  ↑↓=翻输入历史  Esc=取消  Ctrl+L=清屏  Ctrl+C=退出
补全: 输入 / 列命令、@ 列文件；↑↓ 选、Tab 补全（再按轮换）、回车执行/接受、Esc 收起；继续输入可筛选"""

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


class PaletteView(Static):
    """补全面板：候选行可点击——点击=选中并接受（与 Tab 同义），末尾提示行忽略。"""

    def on_click(self, event) -> None:
        try:
            self.app._palette_click(int(event.y))
        except Exception:  # noqa: BLE001 —— 点击失败绝不影响输入
            pass


class PromptEditor(TextArea):
    """opencode 式多行输入框：回车提交、Shift+Enter/Ctrl+J 换行、随内容自动长高（1~6 行）。

    对外提供 .value / .cursor_position 属性，兼容原 Input 的全部调用点。
    ↑↓/Tab/Esc 在这里按上下文分流给 app（补全面板选择 / 历史 / 切模式 / 取消）——
    TextArea 自己会吞这些键（光标移动/缩进），必须在进 super 前拦截。
    """

    class Submitted(Message):
        """回车提交（等价原 Input.Submitted，供 app 的 on_prompt_editor_submitted）。"""

        def __init__(self, editor: "PromptEditor", value: str) -> None:
            super().__init__()
            self.editor = editor
            self.value = value

    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, v: str) -> None:
        self.text = v or ""
        self.move_cursor(self.document.end)
        self.post_message(TextArea.Changed(self))   # 程序化赋值不自动发 Changed → 手动补发，
                                                    # 否则 Tab 接受候选后面板/高度不刷新

    @property
    def cursor_position(self) -> int:           # 兼容旧 Input 接口（调用点只用它"置尾"）
        return len(self.text)

    @cursor_position.setter
    def cursor_position(self, _pos) -> None:
        self.move_cursor(self.document.end)

    async def _on_key(self, event) -> None:
        app = self.app
        key = event.key
        if app._inline_confirm_active():
            event.stop()
            event.prevent_default()
            app._handle_inline_confirm_key(key)
            return
        if key == "enter":                       # 回车=提交（换行用 Ctrl+J）
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self, self.text))
            return
        if key in ("ctrl+j", "shift+enter"):     # 换行（shift+enter 依终端协议，能收到就支持）
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if key in ("pageup", "ctrl+u"):          # mouse=False 时滚轮不可用；键盘滚结果区
            event.stop()
            event.prevent_default()
            app.action_scroll_log_up()
            return
        if key in ("pagedown", "ctrl+d"):
            event.stop()
            event.prevent_default()
            app.action_scroll_log_down()
            return
        if key == "ctrl+p":
            event.stop()
            event.prevent_default()
            app.action_history_prev()
            return
        if key == "ctrl+n":
            event.stop()
            event.prevent_default()
            app.action_history_next()
            return
        if key == "tab":                         # Tab 不缩进：补全/切模式（与全局键位一致）
            event.stop()
            event.prevent_default()
            app.action_toggle_mode()
            return
        if key == "escape":                      # Esc：收面板 / 取消任务（app 统一裁决）
            event.stop()
            event.prevent_default()
            app.action_cancel()
            return
        at_first = self.cursor_location[0] == 0
        at_last = self.cursor_location[0] >= self.document.line_count - 1
        if key == "up" and not app._palette_visible() and self.text == "" and app._history_idx is None:
            event.stop()
            event.prevent_default()
            app.action_scroll_log_line_up()
            return
        if key == "down" and not app._palette_visible() and self.text == "" and app._history_idx is None:
            event.stop()
            event.prevent_default()
            app.action_scroll_log_line_down()
            return
        if key == "up" and (app._palette_visible() or at_first):
            event.stop()                         # 面板选择/翻历史；多行中间行仍是光标上移
            event.prevent_default()
            app.action_history_prev()
            return
        if key == "down" and (app._palette_visible() or at_last):
            event.stop()
            event.prevent_default()
            app.action_history_next()
            return
        await super()._on_key(event)


class ListPicker(ModalScreen):
    """opencode 式选择弹窗：↑↓/点击 选、回车确认、Esc 取消、顶部输入即筛选。

    items=[(value, label)]；dismiss 返回选中的 value（取消返回 None）。
    on_highlight（可选）：高亮变化即回调 value——给 /theme 做实时预览用。
    """

    CSS = """
    ListPicker { align: center middle; }
    #lp-box { width: 76; max-height: 80%; border: round $accent; background: $surface; padding: 1 1; }
    #lp-title { color: $text; text-style: bold; padding: 0 1; }
    #lp-filter { border: round $panel; }
    #lp-list { height: auto; max-height: 14; }
    #lp-hint { color: $text-muted; padding: 0 1; }
    """
    BINDINGS = [
        Binding("escape", "cancel", "取消"),
        Binding("up", "hl_up", show=False),
        Binding("down", "hl_down", show=False),
    ]

    def __init__(self, title: str, items: list, on_highlight=None, initial=None):
        super().__init__()
        self._title = title
        self._items = list(items)
        self._on_highlight = on_highlight
        self._initial = initial             # 打开时高亮的 value（当前主题/模型），不给则第一项

    def compose(self) -> ComposeResult:
        from textual.widgets import OptionList
        with Vertical(id="lp-box"):
            yield Static(self._title, id="lp-title")
            yield Input(placeholder="输入筛选…", id="lp-filter")
            yield OptionList(id="lp-list")
            yield Static("↑↓/点击 选 · 回车确认 · Esc 取消", id="lp-hint")

    def on_mount(self) -> None:
        self._refill("")
        self.query_one("#lp-filter", Input).focus()   # 焦点在筛选框：直接打字即筛，↑↓ 走绑定

    def _list(self):
        from textual.widgets import OptionList
        return self.query_one("#lp-list", OptionList)

    def _refill(self, needle: str) -> None:
        from textual.widgets.option_list import Option
        ol = self._list()
        ol.clear_options()
        nl = needle.strip().lower()
        shown = []
        for val, label in self._items:
            if not nl or nl in str(val).lower() or nl in str(label).lower():
                ol.add_option(Option(label, id=str(val)))
                shown.append(str(val))
        if shown:                           # 首次打开高亮"当前项"（否则 /theme 一打开就预览成第一项）
            want = str(self._initial) if self._initial is not None else None
            ol.highlighted = shown.index(want) if want in shown else 0
            self._initial = None            # 只对首次生效；之后筛选回到第一项

    def _move(self, step: int) -> None:
        ol = self._list()
        if ol.option_count:
            ol.highlighted = ((ol.highlighted or 0) + step) % ol.option_count

    def action_hl_up(self) -> None:
        self._move(-1)

    def action_hl_down(self) -> None:
        self._move(1)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()                                  # 筛选框事件别冒泡进 app 的输入处理
        self._refill(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        ol = self._list()                             # 回车 = 选当前高亮
        if ol.option_count and ol.highlighted is not None:
            self.dismiss(ol.get_option_at_index(ol.highlighted).id)

    def on_option_list_option_selected(self, event) -> None:
        self.dismiss(event.option.id)                 # 点击/在列表上回车

    def on_option_list_option_highlighted(self, event) -> None:
        if self._on_highlight is not None and event.option is not None:
            try:
                self._on_highlight(event.option.id)   # 实时预览（/theme 用）
            except Exception:  # noqa: BLE001
                pass


class ConfirmScreen(ModalScreen[bool]):
    """写分支前的确认弹窗（对齐 opencode 的权限确认 / 项目“人在关口”理念）。"""

    CSS = """
    ConfirmScreen { align: center middle; }
    #dialog { width: 64; height: auto; border: thick $warning; background: $surface; padding: 1 2; }
    #confirm-hint { color: $text-muted; margin-top: 1; }
    #confirm-actions { height: 3; margin-top: 1; }
    #confirm-actions Button { margin-right: 2; }
    """
    BINDINGS = [
        Binding("enter", "choose", "执行当前选项"),
        Binding("left", "prev_choice", "上一个"),
        Binding("right", "next_choice", "下一个"),
        Binding("tab", "next_choice", "下一个"),
        Binding("y", "yes", "确认"),
        Binding("a", "always", "始终允许"),
        Binding("n", "no", "取消"),
        Binding("escape", "no", "取消"),
    ]

    def __init__(self, message: str, scope: str = "writes"):
        super().__init__()
        self._message = message
        self._scope = scope           # "始终允许"的作用域：writes（写文件）/ commands（跑命令），各自独立
        self._choices = ["confirm-yes", "confirm-always", "confirm-no"]
        self._choice_idx = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self._message, id="confirm-msg")
            with Horizontal(id="confirm-actions"):
                yield Button("确认", id="confirm-yes", variant="success")
                yield Button("本会话始终允许", id="confirm-always", variant="warning")
                yield Button("取消", id="confirm-no")
            yield Static("←/→ 或 Tab 选择 · Enter 执行 · y 确认 · a 本会话始终允许 · n/Esc 取消", id="confirm-hint")

    def on_mount(self) -> None:
        self._focus_choice()

    def _focus_choice(self) -> None:
        try:
            self.query_one(f"#{self._choices[self._choice_idx]}", Button).focus()
        except Exception:  # noqa: BLE001
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "confirm-yes":
            self.action_yes()
        elif bid == "confirm-always":
            self.action_always()
        elif bid == "confirm-no":
            self.action_no()

    def action_prev_choice(self) -> None:
        self._choice_idx = (self._choice_idx - 1) % len(self._choices)
        self._focus_choice()

    def action_next_choice(self) -> None:
        self._choice_idx = (self._choice_idx + 1) % len(self._choices)
        self._focus_choice()

    def action_choose(self) -> None:
        bid = self._choices[self._choice_idx]
        if bid == "confirm-yes":
            self.action_yes()
        elif bid == "confirm-always":
            self.action_always()
        else:
            self.action_no()

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_always(self) -> None:
        """本会话内后续**同类**操作不再逐个确认（对齐 Claude Code 的 Always allow）。

        作用域隔离：写文件的 [a] 只静默后续写、跑命令的 [a] 只静默后续命令——
        否则为省文件编辑确认按下的 [a] 会连任意 shell 命令一起放行（权限提升）。
        """
        try:
            setattr(self.app, f"_allow_{self._scope}_session", True)
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
    #status { height: 1; color: $text-muted; padding: 0 1; }
    #plan { height: auto; max-height: 10; overflow-y: auto; border: round $accent;
            background: $surface; padding: 0 1; }
    #palette { height: auto; max-height: 9; overflow-y: auto; background: $surface;
               color: $text-muted; padding: 0 1; }
    #statusbar { height: 1; color: $text-muted; background: $surface; padding: 0 1; }
    #prompt { border: round $panel; height: 3; }
    #prompt:focus { border: round $accent; }
    #prompt .text-area--placeholder { color: $text-muted; }
    """
    BINDINGS = [
        Binding("ctrl+c", "quit", "退出", priority=True),
        Binding("escape", "cancel", "取消"),
        Binding("tab", "toggle_mode", "补全/切模式"),
        Binding("pageup", "scroll_log_up", "上翻", show=False),
        Binding("pagedown", "scroll_log_down", "下翻", show=False),
        Binding("ctrl+u", "scroll_log_up", "上翻", show=False),
        Binding("ctrl+d", "scroll_log_down", "下翻", show=False),
        Binding("ctrl+p", "history_prev", "上一条", show=False),
        Binding("ctrl+n", "history_next", "下一条", show=False),
        Binding("up", "history_prev", "上一条", show=False),
        Binding("down", "history_next", "下一条", show=False),
        Binding("ctrl+l", "clear_log", "清屏"),
    ]

    def __init__(self, repo_root: str = ".", attach: str | None = None):
        super().__init__()
        self.repo_root = repo_root
        self._attach_url = attach           # 非 None = attach 模式：回合交常驻 serve 跑（协议客户端）
        self.mode = "plan"                  # plan | build
        self.transcript: list[str] = []     # 完整记录，便于回看与测试
        # 会话持久化（SQLite）：对话落盘，可 /sessions 列出、/resume 恢复。
        # TUI 启动即写 .vortocode 生成态（sessions.db/tui_history）→ 先放自忽略 .gitignore（防足迹）。
        from src.agents.dev_plan import ensure_state_gitignore
        ensure_state_gitignore(repo_root)
        self.sessions = SessionManager(str(Path(repo_root) / ".vortocode" / "sessions.db"))
        self.session_id: str | None = None
        self._persist_on = False            # 开场白阶段先不落盘
        self._session_last_user = ""        # 用于生成 /sessions 的轻量摘要
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
        self._pal_items: list = []          # 补全面板候选 [(显示文本, 说明)]（空=面板隐藏）
        self._pal_accepts: list[str] = []   # 各候选被接受后的完整输入值（与 _pal_items 对齐）
        self._pal_idx = 0                   # 当前高亮候选（↑↓/点击 移动，Tab 补全，回车执行/接受）
        self._pal_kind = ""                 # "命令" / "文件"（回车语义不同：执行 vs 接受）
        self._pal_start = 0                 # 开窗起点（渲染时更新；点击换算行号用）
        self._confirm_future = None         # 内联权限确认：不弹 modal，显示在输入框上方选择区
        self._confirm_callback = None
        self._confirm_scope = "writes"
        self._confirm_message = ""
        self._confirm_idx = 0
        self._queued_inputs: list[str] = []  # 忙时提交的消息排队（回合结束自动发送，不再丢弃）
        self._allow_writes_session = False  # 本会话"始终允许"写操作（ConfirmScreen 的 [a]，scope=writes）
        self._allow_commands_session = False  # 本会话"始终允许"跑命令（独立作用域，不吃写豁免）
        self._speak_replies = False         # /speak 开关：开则把每条回复合成语音朗读（mimo-v2.5-tts）
        self._user_cmds = None              # 用户自定义命令缓存（.vortocode/commands，惰性加载、/commands reload 重扫）
        self._context_policy = self._load_setting("context_policy", "auto")
        self._auto_memory = bool(self._load_setting("auto_memory", True))
        self._last_memory_candidate = ""
        self._turn_tools = 0                # 本回合工具调用计数（回合结束给"✓ 完成"反馈）
        self._turn_tool_lines: list[str] = []   # 本回合 🔧 工具行（结果流预览，收尾折叠）
        self._turn_tool_counts: dict[str, int] = {}  # 工具名 → 次数（折叠摘要用）
        self._turn_tool_previewed = 0       # 已写入结果流的工具预览数（避免长回合刷屏）
        self._last_dev = None              # 最近一次 dev 流水线产出 {workspace, files}，供 /apply
        # 常驻状态栏缓存：仓库/分支/dirty/PR 由后台 worker 异步刷新，render 只读缓存（不阻塞 UI）
        self._sb = {"branch": "", "dirty": False, "pr": "", "model": _model_name(),
                    "pr_branch": None, "pr_on": True}
        self._sb_last = ""                  # 最近一次状态栏渲染出的纯文本（测试/调试用）
        self._plan_last = ""                # 最近一次计划面板渲染出的文本（测试/版本无关地读取）
        self._model_override = None         # /model 切换的模型（本会话覆盖 .env 的 DEFAULT_MODEL）
        self._show_thinking = True          # 思考呈现开关（/think 切；推理型模型的过程提示进结果区）

    # ---------------------------------------------------------------- 布局
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="log", wrap=True, markup=True, highlight=False, auto_scroll=True)
        yield Static(id="status")
        # 常驻任务清单面板：update_plan 更新即重渲、钉在输入框上方不随日志滚走（对标 CC 的 TODO 常显）。
        yield Static(id="plan")
        yield PaletteView(id="palette")
        yield Static(id="statusbar")
        # opencode 式多行编辑器（回车提交 / Ctrl+J 换行 / 自动长高）；补全提示全靠面板，
        # 不做幽灵文字——↑↓ 选了 /run 幽灵还写 /analyze 的打架问题在结构上消失。
        yield PromptEditor(placeholder="输入需求（自然语言），或 / 看命令…  · Shift+Enter/Ctrl+J 换行",
                           id="prompt")

    def on_mount(self) -> None:
        from src.llm.client import reset_usage
        reset_usage()                       # 每个会话从零计量
        self._load_history()                # 跨会话输入历史（↑/↓ 可调出上次的）
        self._load_theme()                  # 套用上次选的配色主题
        self.query_one("#status", Static).display = False
        self.query_one("#plan", Static).display = False    # 无计划时不占地方，update_plan 后才现身
        self.query_one("#palette", Static).display = False
        self._greet()
        self._sync_subtitle()
        self.session_id = self.sessions.start_session()
        self._persist_on = True            # 之后的对话才落盘（不存开场白）
        self.query_one("#prompt", PromptEditor).focus()
        # 常驻状态栏：先渲染缓存（仓库/模型/模式立显）。后台轮询（git 分支/dirty + gh PR）只在
        # 真终端起——无头(run_test)下不起定时器/worker，免得每个 TUI 测试都白跑 git/gh、拖慢测试。
        self._render_statusbar()
        if not self.is_headless:
            self._refresh_git()
            self.set_interval(4.0, self._refresh_git)   # 分支/改动：勤刷（本地 git，快）
            self.set_interval(30.0, self._refresh_pr)   # PR 状态：慢刷（gh 走网络）

    def on_unmount(self) -> None:
        """退出时收摊：停掉所有后台命令，别把 dev server/watcher 进程泄漏成孤儿。"""
        try:
            from src.agents.shell import stop_all_background
            stop_all_background()
        except Exception:  # noqa: BLE001
            pass

    def _greet(self) -> None:
        """首跑引导：压缩成几行，留出更多真实对话空间。"""
        import os
        self._chrome("[b]VortoCode[/b] · 交互式 AI 开发助手 [dim](/ 看命令，@ 带上下文)[/dim]")
        self._chrome(f"[{self._tc('text-primary', '#8ab4f8')}]试试：[/] "
                     "[b]这个项目是做什么的？[/b]   ·   "
                     "[b]@src/cli.py 讲讲这个文件[/b]   ·   [b]给 xx 模块补测试[/b]")
        self._chrome("[dim]plan=只读/提案，build=可写分支；Tab 切换，↑↓ 历史，Ctrl+J 换行，/help 全部命令。[/dim]")
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

    # ---- 回合内工具活动：主结果流里少量预览，收尾折叠成一行摘要 ----
    def _turn_say(self, markup: str) -> None:
        """回合内 say 路由：🔧 工具行进入当前 turn timeline 的轻量预览，长回合只计数；
        其余提示（里程碑/警告/压缩说明等）照旧进对话 log。"""
        try:
            plain = Text.from_markup(str(markup)).plain
        except Exception:  # noqa: BLE001 —— 非法标记按原文处理
            plain = str(markup)
        s = plain.strip()
        if s.startswith("🔧"):
            name = (s[1:].strip().split() or ["?"])[0]
            self._turn_tool_counts[name] = self._turn_tool_counts.get(name, 0) + 1
            self._turn_tool_lines.append(s)
            self._preview_tool_activity(s)
            return
        self._chrome(markup)

    def _preview_tool_activity(self, line: str) -> None:
        """把本回合前几条工具活动写进主结果区；更多工具留给收尾摘要和 /audit。"""
        if self._turn_tool_previewed >= 5:
            return
        self._turn_tool_previewed += 1
        self.transcript.append(line)
        self.query_one("#log", RichLog).write(Text(line, style="dim"))

    def _fold_tool_activity(self) -> None:
        """回合收尾：把整回合工具活动折叠成一行摘要写进对话区（成败都写，
        错误/取消也不丢工具轨迹；完整参数见 /audit）。"""
        if not self._turn_tool_counts:
            return
        total = sum(self._turn_tool_counts.values())
        parts = " ".join(f"{n}×{c}" for n, c in self._turn_tool_counts.items())
        self._chrome(f"[dim]🔧 {total} 个工具调用 · {parts} · 详情 /audit[/dim]")
        self._turn_tool_lines.clear()
        self._turn_tool_counts.clear()
        self._turn_tool_previewed = 0

    def _emit(self, text: str) -> None:
        """工具/命令输出（按字面写，避免 [xxx] 被当成标记解析）。"""
        self.transcript.append(text)
        self.query_one("#log", RichLog).write(Text(text))
        self._persist(text, markup=False)

    def _make_stream_preview(self, *, label: str = "vorto", max_updates: int = 8):
        """Create a throttled stream preview writer that appends to the main result log.

        RichLog is append-oriented, so streaming is represented as sparse snapshots in
        the same turn timeline. The final assistant message still lands normally.
        """
        state = {"started": False, "last": 0.0, "updates": 0}

        def _preview(partial: str) -> None:
            self.query_one("#status", Static).display = False   # 有正文了，转圈让位
            text = str(partial or "")
            if not text:
                return
            now = time.monotonic()
            if state["started"] and state["updates"] >= max_updates:
                return
            if state["started"] and now - state["last"] < 0.8:
                return
            tail = "\n".join(text[-1200:].splitlines()[-6:]).strip()
            if not tail:
                return
            if not state["started"]:
                state["started"] = True
                line = f"● {label}\n{tail}"
            else:
                line = f"  {tail}"
                state["updates"] += 1
            state["last"] = now
            self.transcript.append(tail)
            self.query_one("#log", RichLog).write(Text(line, style="dim"))

        return _preview

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
        self._session_last_user = self._summary_text(text)
        self._ensure_session_title(text)
        self._update_session_summary(f"用户：{self._session_last_user}")

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
        reply = self._summary_text(text)
        if self._session_last_user:
            self._update_session_summary(f"用户：{self._session_last_user} · 回复：{reply}")
        elif reply:
            self._update_session_summary(f"回复：{reply}")
        self._maybe_offer_auto_memory()

    def _persist(self, content: str, markup: bool) -> None:
        """把一条对话写进当前会话（落盘）。失败不影响交互。"""
        if not self._persist_on:
            return
        try:
            self.sessions.add_message("assistant", content, metadata={"markup": markup})
        except Exception:  # noqa: BLE001
            pass

    def _summary_text(self, text: str, limit: int = 72) -> str:
        """用于 session 列表的确定性短摘要：去控制符/换行/富文本噪声，截断。"""
        plain = re.sub(r"\[[/?][^\]]+\]", "", str(text or ""))
        plain = " ".join(plain.replace("\n", " ").split())
        return (plain[:limit - 1] + "…") if len(plain) > limit else plain

    def _ensure_session_title(self, text: str) -> None:
        """首条真实用户输入给 session 起可读标题，避免 /sessions 只剩 id。"""
        if not (self._persist_on and self.session_id):
            return
        try:
            cur = self.sessions.store.get_session(self.session_id) or {}
            name = str(cur.get("name") or "")
            if name and not name.startswith("Session "):
                return
            title = self._summary_text(text, 42) or self.session_id
            self.sessions.store.update_session(self.session_id, name=title)
        except Exception:  # noqa: BLE001
            pass

    def _update_session_summary(self, summary: str) -> None:
        """把最近一轮摘要和轻量运行上下文写到 sessions.metadata，供 /sessions 和 /resume 辨认。"""
        if not (self._persist_on and self.session_id):
            return
        try:
            cur = self.sessions.store.get_session(self.session_id) or {}
            try:
                md = json.loads(cur.get("metadata") or "{}")
            except Exception:  # noqa: BLE001
                md = {}
            md["summary"] = self._summary_text(summary, 110)
            if self._session_last_user:
                md["last_user"] = self._session_last_user
            if "回复：" in summary:
                md["last_reply"] = self._summary_text(summary.rsplit("回复：", 1)[-1], 88)
            md["mode"] = self.mode
            branch = str(self._sb.get("branch") or "")
            if branch:
                md["branch"] = branch
            md["dirty"] = bool(self._sb.get("dirty"))
            if self.agent is not None:
                try:
                    usage = self.agent.context_usage(self.mode)
                    md["context"] = {
                        "pct": int(usage.get("pct", 0)),
                        "policy": str(usage.get("policy") or ""),
                        "history_messages": int(usage.get("history_messages", 0)),
                    }
                except Exception:  # noqa: BLE001
                    pass
            self.sessions.store.update_session(self.session_id, metadata=json.dumps(md, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass

    def _session_metadata(self, row: dict | None) -> dict:
        try:
            data = json.loads((row or {}).get("metadata") or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _session_picker_label(self, row: dict) -> str:
        md = self._session_metadata(row)
        name = row.get("name") or row.get("id")
        bits = []
        if md.get("mode"):
            bits.append(str(md["mode"]))
        if md.get("branch"):
            bits.append(str(md["branch"]) + ("*" if md.get("dirty") else ""))
        ctx = md.get("context") if isinstance(md.get("context"), dict) else {}
        if ctx.get("pct") is not None and ctx.get("policy"):
            bits.append(f"ctx {ctx.get('pct')}%/{ctx.get('policy')}")
        meta = f" [{' · '.join(bits)}]" if bits else ""
        summary = md.get("summary") or md.get("last_user") or ""
        suffix = f" — {summary}" if summary else ""
        return f"{name}{meta}{suffix}"

    def _resume_context_text(self, row: dict | None) -> str:
        md = self._session_metadata(row)
        lines = ["↻ 已恢复会话"]
        if row:
            lines.append(f"  session: {row.get('name') or row.get('id')} ({row.get('id')})")
        if md.get("mode") or md.get("branch"):
            mode = md.get("mode") or "?"
            branch = md.get("branch") or "?"
            dirty = "*" if md.get("dirty") else ""
            lines.append(f"  上次状态: {mode} · {branch}{dirty}")
        if md.get("last_user"):
            lines.append(f"  最后用户: {md['last_user']}")
        if md.get("last_reply"):
            lines.append(f"  最后回复: {md['last_reply']}")
        elif md.get("summary"):
            lines.append(f"  摘要: {md['summary']}")
        ctx = md.get("context") if isinstance(md.get("context"), dict) else {}
        if ctx:
            lines.append(f"  上下文: {ctx.get('pct', 0)}% · {ctx.get('policy') or 'unknown'}"
                         f" · {ctx.get('history_messages', 0)} messages")
        return "\n".join(lines)

    def _sync_subtitle(self) -> None:
        desc = "只读/提案" if self.mode == "plan" else "可写分支"
        sid = f" · 会话 {self.session_id}" if self.session_id else ""
        busy = " · ⏳运行中(Esc 取消)" if self._busy else ""
        allow = ("" + (" · 写:始终允许✓" if self._allow_writes_session else "")
                 + (" · 命令:始终允许✓" if self._allow_commands_session else ""))
        from src.llm.client import get_usage
        u = get_usage()
        tot = u["total_tokens"]
        tok = (f" · ~{tot // 1000}k tok/{u['calls']}call" if tot >= 1000
               else f" · ~{tot} tok/{u['calls']}call") if u["calls"] else ""
        self.sub_title = f"模式 {self.mode}（{desc}）{sid}{busy}{allow}{tok}"
        self._render_statusbar()           # 模式/用量变化时同步刷新状态栏

    # ---------------------------------------------------------------- 项目设置
    def _settings_file(self) -> Path:
        return Path(self.repo_root) / ".vortocode" / "settings.json"

    def _load_settings(self) -> dict:
        try:
            p = self._settings_file()
            if not p.is_file():
                return {}
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _save_settings(self, data: dict) -> None:
        try:
            p = self._settings_file()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def _load_setting(self, key: str, default=None):
        return self._load_settings().get(key, default)

    def _save_setting(self, key: str, value) -> None:
        data = self._load_settings()
        data[key] = value
        self._save_settings(data)

    # ---------------------------------------------------------------- 状态栏
    def _fmt_tokens_short(self, n: int) -> str:
        if n >= 1000:
            return f"{n / 1000:.1f}k".replace(".0k", "k")
        return str(n)

    def _context_usage_label(self) -> str:
        if self.agent is None:
            return ""
        try:
            u = self.agent.context_usage(self.mode)
            used = self._fmt_tokens_short(int(u["used_tokens"]))
            limit = self._fmt_tokens_short(int(u["max_context_tokens"]))
            label = f"ctx {used}/{limit} {int(u['pct'])}%"
            policy = str(u.get("policy") or "").strip()
            if policy:
                label += f" · policy {policy}"
            return label
        except Exception:  # noqa: BLE001
            return ""

    def _render_statusbar(self) -> None:
        """渲染常驻底部状态栏：仓库 · 分支(改动) · PR · 模型 · 模式 · token。只读缓存，不触网/不阻塞。"""
        try:
            bar = self.query_one("#statusbar", Static)
        except Exception:  # noqa: BLE001 —— 组件还没挂载（早于 compose）就跳过
            return
        sb = self._sb
        try:
            repo = Path(self.repo_root).resolve().name or str(self.repo_root)
        except Exception:  # noqa: BLE001
            repo = str(self.repo_root)
        parts = [f"📁 {repo}"]
        if sb.get("branch"):
            parts.append(f"⎇ {sb['branch']}" + ("*" if sb.get("dirty") else ""))
        if sb.get("pr"):
            parts.append(sb["pr"])
        parts.append(f"🧠 {sb.get('model', '?')}")
        parts.append("🟢 build" if self.mode == "build" else "🔵 plan")
        ctx = self._context_usage_label()
        if ctx:
            parts.append(ctx)
        from src.llm.client import get_usage
        u = get_usage()
        if u["calls"]:
            tot = u["total_tokens"]
            parts.append(f"~{tot // 1000}k tok" if tot >= 1000 else f"~{tot} tok")
        line = " · ".join(parts)
        self._sb_last = line                # 存一份纯文本，便于测试/版本无关地读取当前状态栏
        bar.update(Text(line, style="dim"))

    @work(thread=True, exclusive=True, group="sb-git")
    def _refresh_git(self) -> None:
        """后台拉当前 git 分支 + 是否有未提交改动（本地、快），写缓存后回主线程重绘状态栏。

        一次 `git status -b --porcelain` 同时拿到分支（首行 `## ...`）和改动（其余行），省一半子进程。
        """
        import subprocess
        try:
            r = subprocess.run(["git", "-C", str(self.repo_root), "status", "--porcelain=v1", "--branch"],
                               capture_output=True, text=True, timeout=3)
        except Exception:  # noqa: BLE001 —— 无 git 二进制：状态栏就不显示分支
            return
        if r.returncode != 0:                  # 非 git 仓库等
            return
        lines = r.stdout.splitlines()
        branch = ""
        if lines and lines[0].startswith("## "):
            head = lines[0][3:]
            if head.startswith("No commits yet on "):
                branch = head[len("No commits yet on "):].strip()
            elif head.startswith("HEAD (no branch)"):
                branch = "HEAD"
            else:
                branch = head.split("...", 1)[0].split(" ", 1)[0]   # "br...origin/br [ahead]" → "br"
        dirty = any(not ln.startswith("## ") for ln in lines)
        self._sb["branch"], self._sb["dirty"] = branch, dirty
        self.call_from_thread(self._render_statusbar)

    @work(thread=True, exclusive=True, group="sb-pr")
    def _refresh_pr(self) -> None:
        """后台用 gh 查当前分支的 PR 状态（走网络、慢）。没装 gh 就关掉、不再轮询；失败静默。"""
        import json
        import subprocess
        if not self._sb.get("pr_on", True):
            return
        branch = self._sb.get("branch")
        if not branch or branch == "HEAD":
            return
        try:
            r = subprocess.run(
                ["gh", "pr", "list", "--head", branch, "--state", "all",
                 "--json", "number,state", "--limit", "1"],
                cwd=str(self.repo_root), capture_output=True, text=True, timeout=8)
        except FileNotFoundError:
            self._sb["pr_on"] = False        # 没装 gh：关掉 PR 轮询，省得每 30s 白跑
            return
        except Exception:  # noqa: BLE001 —— 超时/其它：本次跳过，下次再试
            return
        pr = ""
        if r.returncode == 0 and r.stdout.strip():
            try:
                data = json.loads(r.stdout)
                if data:
                    pr = f"PR #{data[0]['number']} {str(data[0].get('state', '')).lower()}"
            except Exception:  # noqa: BLE001
                pr = ""
        self._sb["pr"], self._sb["pr_branch"] = pr, branch
        self.call_from_thread(self._render_statusbar)

    _COMMON_MODELS = ["mimo-v2.5", "mimo-v2.5-pro", "mimo-v2-pro", "mimo-v2-omni",
                      "mimo-v2.5-asr", "mimo-v2.5-tts"]

    def _cmd_model(self, arg: str) -> None:
        """/model：无参弹 opencode 式模型选择器（回车切换，本会话生效）；带参直接切。"""
        arg = (arg or "").strip()
        cur = self._sb.get("model", "?")
        if not arg:
            models = list(self._COMMON_MODELS)
            if cur and cur not in models:
                models.insert(0, cur)
            items = [(m, f"{m}{'  ← 当前' if m == cur else ''}") for m in models]

            def _done(m) -> None:
                if m and m != cur:
                    self._cmd_model(m)      # 复用带参路径（就地改客户端 + 状态栏同步）

            self.push_screen(ListPicker("选择模型 · 本会话生效（重启回 .env 的 DEFAULT_MODEL）",
                                        items, initial=cur), _done)
            return
        self._model_override = arg          # agent 未建时，_build_main_agent 会读它应用
        if self.agent is not None:
            try:
                self.agent.set_model(arg)
            except Exception as e:  # noqa: BLE001
                self._chrome(f"[red]切换模型失败：{e}[/red]")
                return
        self._sb["model"] = arg
        self._render_statusbar()
        self._chrome(f"[green]已切换模型 → {arg}[/green]"
                     "[dim]（本会话后续对话生效；未授权的模型会在下次调用时报 403）[/dim]")

    # 集中管理忙碌态：动作 worker 一进入运行就置忙、结束(成功/失败/取消)即解除
    def on_worker_state_changed(self, event) -> None:
        if getattr(event.worker, "group", None) != "action":
            return
        running = event.state == WorkerState.RUNNING
        self._busy = running
        self._start_status() if running else self._stop_status()
        self._sync_subtitle()
        if event.state in (WorkerState.SUCCESS, WorkerState.ERROR, WorkerState.CANCELLED):
            self._drain_queued()                # 回合真正收尾（终态）才放下一条排队消息

    def _drain_queued(self) -> None:
        """发送一条排队中的消息（一次一条：它的回合结束后本方法会再次被触发，天然接力）。"""
        if self._busy or not self._queued_inputs:
            return
        text = self._queued_inputs.pop(0)
        self._say_user(text)
        self._route(text)

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
        if self._inline_confirm_active():
            self._finish_inline_confirm("no")
            return
        if self._palette_visible():           # 先收补全面板；再按一次 Esc 才是取消任务
            self._hide_palette()
            return
        if self._busy:
            if self._queued_inputs:           # 主动取消 = 连排队的一起清（别取消完又自动冒一条）
                self._chrome(f"[dim]已清空 {len(self._queued_inputs)} 条排队消息[/dim]")
                self._queued_inputs.clear()
            self.workers.cancel_all()
            self._chrome("[yellow]已取消当前操作[/yellow]")

    # ---------------------------------------------------------------- 键位动作
    def action_toggle_mode(self) -> None:
        # Tab 上下文化：补全面板可见 → 接受选中候选（输入已是该值则先跳下一个，shell 式轮换）；
        # 否则切 plan/build 模式。
        if self._inline_confirm_active():
            self._move_inline_confirm(1)
            return
        if self._palette_visible():
            v = self.query_one("#prompt", PromptEditor).value
            if v == self._pal_accepts[self._pal_idx] and len(self._pal_accepts) > 1:
                self._palette_move(1)
            self._palette_accept()
            return
        self._set_mode("build" if self.mode == "plan" else "plan")

    def _set_mode(self, mode: str) -> bool:
        mode = str(mode or "").strip().lower()
        if mode not in ("plan", "build"):
            return False
        if self.mode == mode:
            self._sync_subtitle()
            self._chrome(f"→ 已在 [b]{self.mode}[/b] 模式")
            return True
        self.mode = mode
        self._sync_subtitle()
        self._chrome(f"→ 切到 [b]{self.mode}[/b] 模式")
        self._record_mode_change()
        return True

    def _mode_context(self) -> str:
        desc = "可写分支，写文件/跑命令/开发流水线可用" if self.mode == "build" else "只读/提案，写操作需先切 build"
        return f"[运行时状态]\n当前 TUI 模式：{self.mode}（{desc}）。"

    def _record_mode_change(self) -> None:
        """把模式切换写进 agent 历史，避免上一轮 plan 拒绝记录误导后续回合。"""
        if self.agent is None:
            return
        try:
            self.agent.history.append({
                "role": "user",
                "content": f"{self._mode_context()}\n用户刚刚手动切换了模式；后续回合必须以当前模式为准。",
            })
        except Exception:  # noqa: BLE001
            pass

    def _set_input(self, val: str) -> None:
        inp = self.query_one("#prompt", PromptEditor)
        inp.value = val
        inp.cursor_position = len(val)

    _CONFIRM_CHOICES = [("yes", "确认"), ("always", "本会话始终允许"), ("no", "取消")]

    def _inline_confirm_active(self) -> bool:
        fut = self._confirm_future
        return fut is not None and not fut.done()

    def _begin_inline_confirm(self, message: str, scope: str = "writes", callback=None):
        """在输入框上方显示 Claude Code 式权限选择项，不使用 modal 弹窗。"""
        if self._inline_confirm_active():
            return self._confirm_future
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._confirm_future = fut
        self._confirm_callback = callback
        self._confirm_scope = scope
        self._confirm_message = message
        self._confirm_idx = 0
        self._pal_items, self._pal_accepts, self._pal_kind = [], [], ""
        self._render_inline_confirm()
        try:
            self.query_one("#prompt", PromptEditor).focus()
        except Exception:  # noqa: BLE001
            pass
        return fut

    async def _inline_confirm(self, message: str, scope: str = "writes") -> bool:
        return bool(await self._begin_inline_confirm(message, scope=scope))

    def _render_inline_confirm(self) -> None:
        try:
            panel = self.query_one("#palette", Static)
        except Exception:  # noqa: BLE001
            return
        msg = " ".join(str(self._confirm_message).split())
        if len(msg) > 220:
            msg = msg[:217] + "..."
        t = Text()
        t.append("权限确认  ", style="bold")
        t.append(msg + "\n", style="dim")
        for i, (_action, label) in enumerate(self._CONFIRM_CHOICES):
            if i:
                t.append("  ")
            if i == self._confirm_idx:
                t.append(f"› {label} ", style="reverse bold")
            else:
                t.append(f"  {label} ", style="dim")
        t.append("   ←/→/Tab 选择 · Enter 执行 · y/a/n/Esc", style="dim")
        panel.update(t)
        panel.display = True

    def _move_inline_confirm(self, delta: int) -> None:
        self._confirm_idx = (self._confirm_idx + delta) % len(self._CONFIRM_CHOICES)
        self._render_inline_confirm()

    def _handle_inline_confirm_key(self, key: str) -> None:
        if key in ("left", "up"):
            self._move_inline_confirm(-1)
        elif key in ("right", "down", "tab"):
            self._move_inline_confirm(1)
        elif key == "enter":
            self._finish_inline_confirm(self._CONFIRM_CHOICES[self._confirm_idx][0])
        elif key == "y":
            self._finish_inline_confirm("yes")
        elif key == "a":
            self._finish_inline_confirm("always")
        elif key in ("n", "escape"):
            self._finish_inline_confirm("no")

    def _finish_inline_confirm(self, action: str) -> None:
        fut = self._confirm_future
        callback = self._confirm_callback
        result = action in ("yes", "always")
        if action == "always":
            try:
                setattr(self, f"_allow_{self._confirm_scope}_session", True)
            except Exception:  # noqa: BLE001
                pass
        self._confirm_future = None
        self._confirm_callback = None
        self._confirm_message = ""
        self._confirm_idx = 0
        try:
            self.query_one("#palette", Static).display = False
        except Exception:  # noqa: BLE001
            pass
        if fut is not None and not fut.done():
            fut.set_result(result)
        if callback is not None:
            callback(result)

    async def _confirm_write(self, message: str) -> bool:
        """写操作确认门：本会话已选"始终允许"则直接放行，否则弹 ConfirmScreen。

        统一所有写工具(edit/write/save_skill/制品/分支)的确认，支持 [a] 始终允许（仿 CC）。
        """
        if self._allow_writes_session:
            return True
        return await self._inline_confirm(message, scope="writes")

    def _taint_msg(self, message: str) -> str:
        """污点态（本回合摄入过网页/搜索/MCP 外部内容）下给对外操作确认加警示前缀（D0 防提示注入）。"""
        from src.agents.taint import is_tainted
        if is_tainted():
            return ("⚠ 本回合已摄入外部内容（网页/搜索/MCP），下面是对外操作，"
                    "请人工核对是否确是你的本意（防提示注入）：\n" + message)
        return message

    async def _confirm_outward(self, message: str) -> bool:
        """外向操作（push / 开 PR 等推到远端的动作）确认：**始终弹窗**，不吃"始终允许写"的豁免。"""
        return await self._inline_confirm(self._taint_msg(message), scope="writes")

    async def _confirm_command(self, message: str) -> bool:
        """任意 shell 命令确认门：**独立作用域**，不吃"始终允许写文件"的豁免。

        否则用户为省文件编辑逐条确认按下的 [a]，会静默放行后续所有任意命令（=权限提升）。
        本会话对命令单独选过"始终允许"（scope=commands）才免确认。
        污点态（本回合摄入过外部内容）下**无视命令'始终允许'、强制弹确认**（D0 防提示注入外发）。
        """
        from src.agents.taint import is_tainted
        if self._allow_commands_session and not is_tainted():
            return True
        return await self._inline_confirm(self._taint_msg(message), scope="commands")

    def action_history_prev(self) -> None:
        """↑：补全面板可见时选上一个候选；否则调出上一条历史输入（编辑过则当作新输入）。"""
        if self._palette_visible():
            self._palette_move(-1)
            return
        if not self._history:
            return
        inp = self.query_one("#prompt", PromptEditor)
        if self._history_idx is not None and inp.value != self._history[self._history_idx]:
            self._history_idx = None
        if self._history_idx is None:
            self._history_draft = inp.value
            self._history_idx = len(self._history)
        if self._history_idx > 0:
            self._history_idx -= 1
            self._set_input(self._history[self._history_idx])

    def action_history_next(self) -> None:
        """↓：补全面板可见时选下一个候选；否则回到下一条历史，到底恢复草稿。"""
        if self._palette_visible():
            self._palette_move(1)
            return
        if self._history_idx is None:
            return
        inp = self.query_one("#prompt", PromptEditor)
        if inp.value != self._history[self._history_idx]:
            self._history_idx = None
            return
        if self._history_idx < len(self._history) - 1:
            self._history_idx += 1
            self._set_input(self._history[self._history_idx])
        else:
            self._history_idx = None
            self._set_input(self._history_draft)

    def action_scroll_log_up(self) -> None:
        """PageUp/Ctrl+U：焦点留在输入框，向上翻结果区。"""
        try:
            self.query_one("#log", RichLog).scroll_page_up(animate=False)
        except Exception:  # noqa: BLE001
            pass

    def action_scroll_log_down(self) -> None:
        """PageDown/Ctrl+D：焦点留在输入框，向下翻结果区。"""
        try:
            self.query_one("#log", RichLog).scroll_page_down(animate=False)
        except Exception:  # noqa: BLE001
            pass

    def action_scroll_log_line_up(self) -> None:
        """鼠标滚轮在 mouse=False 终端里常退化为 ↑：映射为结果区细滚动。"""
        try:
            self.query_one("#log", RichLog).scroll_up(animate=False, immediate=True)
        except Exception:  # noqa: BLE001
            pass

    def action_scroll_log_line_down(self) -> None:
        """鼠标滚轮在 mouse=False 终端里常退化为 ↓：映射为结果区细滚动。"""
        try:
            self.query_one("#log", RichLog).scroll_down(animate=False, immediate=True)
        except Exception:  # noqa: BLE001
            pass

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
    async def on_prompt_editor_submitted(self, event: "PromptEditor.Submitted") -> None:
        text = _sanitize_input(event.value).strip()   # 剔除漏进的终端转义序列，防脏字符进 agent/API
        if self._palette_visible():             # 回车语义：命令=执行选中项；文件=接受进输入框继续写
            sel = self._pal_accepts[self._pal_idx]
            if self._pal_kind == "文件":
                if sel != event.value:
                    self._set_input(sel)        # 只接受文件路径，不提交（通常还要接着写需求）
                    return
            elif sel in ARG_SLASH_CMDS and text != sel:
                # 从候选面板选择必带参数命令时补成 "/cmd " 让用户填参数；用户已完整输入
                # "/cmd" 并回车时仍执行命令本身，让命令给出用法提示。
                self._set_input(sel + " ")
                return
            else:
                text = sel                      # 执行面板里选中的命令（输入未敲全也可回车）
        self.query_one("#prompt", PromptEditor).value = ""
        self._hide_palette()
        self._history_idx = None                # 提交后退出历史浏览
        if not text:
            return
        if not self._history or self._history[-1] != text:
            self._history.append(text)          # 记入输入历史（去重相邻）
            self._append_history(text)          # 跨会话持久化
        if text.startswith("/"):
            self._say_user(text)
            self._dispatch(text)
        elif self._busy:
            # 排队而非丢弃：此前是回显后直接丢（看着像发出去了、实际没处理——真机 dogfood 抓的）。
            # 回显推迟到真正发送时做，免得 transcript 顺序骗人。
            self._queued_inputs.append(text)
            self._chrome(f"[dim]⏳ 已排队（{len(self._queued_inputs)} 条）—— 当前回合结束后自动发送；"
                         "Esc 取消当前回合并清空队列[/dim]")
        else:
            if self._maybe_offer_build_before_route(text):
                return
            self._continue_text_route(text)

    def _continue_text_route(self, text: str) -> None:
        self._say_user(text)
        self._route(text)           # 普通话：先判意图（闲聊/提问 vs 开发需求）再分流

    def _looks_like_build_intent(self, text: str) -> bool:
        """保守判断用户是否已经在要求动手改项目；命中时先询问切 build。"""
        s = text.strip().lower()
        if not s:
            return False
        review_words = ("看看", "看一下", "审", "review", "分析", "检查", "解释", "为什么", "建议")
        question_only = ("有没有修改", "有没有修复", "是否修改", "是否修复", "改了吗", "修了吗", "修复了吗")
        action_words = (
            "修复", "修一下", "改掉", "修改", "改一下", "实现", "开发", "继续开发", "落地",
            "写入", "写到", "创建", "新增", "添加", "删除", "重构", "提交", "commit",
            "merge", "合并", "push", "开pr", "开 pr",
        )
        if any(w in s for w in question_only):
            return False
        if any(w in s for w in action_words):
            return not (any(w in s for w in review_words) and not any(
                w in s for w in ("帮我修", "修一下", "改掉", "改一下", "实现", "开发", "写入", "提交", "commit", "合并", "merge")))
        return False

    def _maybe_offer_build_before_route(self, text: str) -> bool:
        """plan 下用户明确要动手时，先切 build 再进入同一回合，避免 plan 预算空转。

        返回 True 表示已弹确认，提交流程暂停；弹窗回调会继续同一条输入。
        """
        if self.mode != "plan" or not self._looks_like_build_intent(text):
            return False
        if self._allow_writes_session:
            ok = True
        else:
            def _done(ok: bool | None) -> None:
                if ok and self.mode != "build":
                    self.mode = "build"
                    self._sync_subtitle()
                    self._chrome("[green]→ 已切到 build 模式并继续[/green]")
                    self._record_mode_change()
                elif not ok:
                    self._chrome("[yellow]继续保持 plan 模式：只做分析/方案，不执行写入。[/yellow]")
                self._continue_text_route(text)

            self._begin_inline_confirm(
                "当前是 plan（只读/提案）模式，但这条需求看起来需要修改项目或执行开发动作。\n"
                "切到 build 模式并用这条需求继续？\n"
                "拒绝后仍会按 plan 模式只给方案/建议。",
                callback=_done)
            return True
        if ok and self.mode != "build":
            self.mode = "build"
            self._sync_subtitle()
            self._chrome("[green]→ 已切到 build 模式并继续[/green]")
            self._record_mode_change()
        return False

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """输入变化：更新补全面板、随内容自动长高；并当场抹掉漏进的终端转义序列。"""
        if getattr(event.text_area, "id", "") != "prompt":
            return
        inp = event.text_area
        clean = _sanitize_input(inp.text)
        if clean != inp.text:                    # 修饰键 CSI 等漏进来了 → 立即清掉，别弄脏显示
            inp.value = clean
            return                               # 重设会再触发 Changed，那次已干净
        # 自动长高：1~6 行内容（+2 边框），超出滚动——opencode 式编辑器手感
        inp.styles.height = min(8, max(3, inp.document.line_count + 2))
        if (self._history_idx is not None        # 编辑了调出的历史 → 当作新输入（恢复补全等常规行为）
                and clean != self._history[self._history_idx]):
            self._history_idx = None
        if self._inline_confirm_active():
            return
        self._update_palette(clean)

    def _update_palette(self, value: str) -> None:
        """输入框上方列出补全候选（↑↓ 选、Tab 补全、回车执行/接受）：/ → 命令；@ → 仓库文件。否则隐藏。"""
        if self._history_idx is not None:                      # 历史浏览中不弹补全（↑↓ 留给翻历史）
            self._hide_palette()
            return
        s = value.strip()
        if s.startswith("/") and " " not in s and "\n" not in s:   # 斜杠命令（内置 + 自定义）
            vl = s.lower()
            info = dict(COMMAND_INFO)
            for n, uc in self._user_commands().items():
                info.setdefault("/" + n, self._user_command_label(uc))
            names = SLASH_COMMANDS + ["/" + n for n in self._user_commands()]
            matches = ([c for c in names if c.startswith(vl)]  # 前缀命中优先
                       + [c for c in names                     # 子串兜底（/dit → /audit）
                          if vl[1:] and vl[1:] in c[1:] and not c.startswith(vl)])
            if matches:
                self._show_palette([(c, info.get(c, "")) for c in matches], list(matches), "命令")
                return
        at = value.rfind("@")                                  # @文件（非 @artifact: 这种带冒号的）
        if at != -1:
            token = value[at + 1:]
            if " " not in token and ":" not in token:
                tl = token.lower()
                files = _repo_files(self.repo_root)
                hits = ([f for f in files if f.lower().startswith(tl)]
                        or [f for f in files if tl in f.lower()])[:50]
                if hits:
                    self._show_palette([(f, "") for f in hits],
                                       [value[:at + 1] + f for f in hits], "文件")
                    return
        self._hide_palette()

    def _show_palette(self, items: list, accepts: list, kind: str) -> None:
        """更新候选并渲染；继续输入筛选时尽量保住已选中的候选（还在列表里就跟着走）。"""
        prev = (self._pal_accepts[self._pal_idx]
                if self._pal_idx < len(self._pal_accepts) else None)
        self._pal_items, self._pal_accepts, self._pal_kind = items, accepts, kind
        self._pal_idx = accepts.index(prev) if prev in accepts else 0
        self._render_palette()

    def _hide_palette(self) -> None:
        self._pal_items, self._pal_accepts, self._pal_idx, self._pal_kind = [], [], 0, ""
        try:
            self.query_one("#palette", Static).display = False
        except Exception:  # noqa: BLE001
            pass

    def _palette_visible(self) -> bool:
        return bool(self._pal_items)

    def _palette_move(self, step: int) -> None:
        """↑↓ 在候选间移动（回绕）。"""
        if self._pal_items:
            self._pal_idx = (self._pal_idx + step) % len(self._pal_items)
            self._render_palette()

    def _palette_click(self, y: int) -> None:
        """面板第 y 行被点击：选中该候选并接受（与 Tab 同义）。提示行/越界忽略。"""
        i = self._pal_start + y
        n_shown = min(self._pal_start + 8, len(self._pal_items)) - self._pal_start
        if not self._pal_items or y < 0 or y >= n_shown:
            return
        self._pal_idx = i
        self._render_palette()
        self._palette_accept()

    def _palette_accept(self) -> str | None:
        """把当前选中候选写进输入框，返回接受后的完整输入值；无候选返回 None。"""
        if not self._pal_accepts:
            return None
        val = self._pal_accepts[self._pal_idx]
        self._set_input(val)
        return val

    def _render_palette(self) -> None:
        """渲染补全面板：高亮选中项（↑↓ 移动），候选超一屏时按选中位置开窗。"""
        items, idx, win = self._pal_items, self._pal_idx, 8
        start = max(0, min(idx - win // 2, len(items) - win))
        self._pal_start = start                       # 点击换算行号用
        rows = []
        for i in range(start, min(start + win, len(items))):
            txt, desc = items[i]
            d = f"  [dim]{desc}[/dim]" if desc else ""
            mark = self._tc("text-primary", "#8ab4f8")
            rows.append(f"[b {mark}]›[/] [b]{txt}[/b]{d}" if i == idx else f"  {txt}{d}")
        pos = f" · {idx + 1}/{len(items)}" if len(items) > win else ""
        act = "回车执行" if self._pal_kind == "命令" else "回车接受"
        rows.append(f"[dim]{self._pal_kind}补全 · ↑↓/点击 选 · Tab 补全 · {act} · Esc 收起{pos}[/dim]")
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
        elif cmd in ("plan", "build"):
            self._set_mode(cmd)
        elif cmd == "model":
            self._cmd_model(arg)
        elif cmd == "think":
            self._show_thinking = not self._show_thinking
            self._chrome(f"[dim]💭 思考呈现已{'开' if self._show_thinking else '关'}"
                         "（推理型模型的思维链；对无 reasoning 的模型无影响）[/dim]")
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
            self._cmd_sessions(arg)
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
        elif cmd == "context":
            self._cmd_context(arg)
        elif cmd == "compact":
            self._cmd_compact(arg)
        elif cmd in ("permissions", "permission"):
            self._cmd_permissions(arg)
        elif cmd == "memory":
            self._cmd_memory(arg)
        elif cmd == "tasks":
            self._cmd_tasks(arg)
        elif cmd == "tools":
            self._cmd_tools()
        elif cmd == "audit":
            self._cmd_audit(arg)
        elif cmd == "artifacts":
            self._cmd_artifacts()
        elif cmd == "diff":
            self._cmd_diff(arg)
        elif cmd == "changes":
            self._cmd_changes(arg)
        elif cmd == "review":
            self._cmd_review(arg)
        elif cmd == "verify":
            self._cmd_verify(arg)
        elif cmd == "preflight":
            self._cmd_preflight(arg)
        elif cmd == "git":
            self._cmd_git(arg)
        elif cmd == "commit":
            self._cmd_commit(arg)
        elif cmd == "pr":
            self._cmd_pr(arg)
        elif cmd == "pr-check":
            self._cmd_pr_check(arg)
        elif cmd == "pr-fix":
            self._cmd_pr_fix(arg)
        elif cmd == "mcp":
            self._cmd_mcp(arg)
        elif cmd == "agents":
            self._cmd_agents()
        elif cmd == "speak":
            self._cmd_speak(arg)
        elif cmd == "commands":
            self._cmd_commands(arg)
        elif cmd == "hooks":
            self._cmd_hooks()
        elif cmd == "runagent":
            if arg:
                self._do_runagent(arg)
            else:
                self._chrome("[red]用法: /runagent <id> <任务>[/red]")
        elif cmd in self._user_commands():
            self._run_user_command(cmd, arg)        # 用户自定义命令：展开模板 → 交给主 agent
        else:
            self._chrome(f"[red]未知命令 /{cmd}[/red] · /help 看命令")

    def _user_command_label(self, uc) -> str:
        """补全/列表里展示自定义命令元数据。"""
        parts = [uc.description]
        if getattr(uc, "argument_hint", ""):
            parts.append(f"args: {uc.argument_hint}")
        if getattr(uc, "mode", ""):
            parts.append(f"mode: {uc.mode}")
        if getattr(uc, "model", ""):
            parts.append(f"model: {uc.model}")
        return " · ".join(p for p in parts if p)

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

    def _append_permission_deny(self, tool: str, pattern: str = "") -> Path:
        cfg = Path(self.repo_root) / ".vortocode" / "permissions.yaml"
        try:
            import yaml
            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) if cfg.is_file() else {}
        except Exception:  # noqa: BLE001
            data = {}
        if not isinstance(data, dict):
            data = {}
        deny = data.get("deny")
        if not isinstance(deny, list):
            deny = []
        deny.append({tool: pattern} if pattern else tool)
        data["deny"] = deny
        cfg.parent.mkdir(parents=True, exist_ok=True)
        try:
            import yaml
            cfg.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            lines = ["deny:"]
            for item in deny:
                if isinstance(item, dict):
                    for k, v in item.items():
                        lines.append(f'  - {k}: "{v}"')
                        break
                else:
                    lines.append(f"  - {item}")
            cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.agent = None          # 新规则立即影响下一次主 agent 构建
        return cfg

    def _permission_effective_text(self) -> str:
        from src.agents.permissions import load_permissions
        agent = self.agent or self._build_main_agent()
        perm = load_permissions(self.repo_root)
        lines = [
            "[b]有效工具权限[/b]",
            f"当前模式: {self.mode}",
            f"本会话始终允许: 写={'yes' if self._allow_writes_session else 'no'} · "
            f"命令={'yes' if self._allow_commands_session else 'no'}",
        ]
        for t in agent._tool_list:
            deny = perm.denied(t.name, {})
            if deny:
                status = "deny"
            elif self.mode == "plan" and not t.read_only:
                status = "needs build"
            elif t.read_only:
                status = "allowed"
            elif t.name == "run_command" and self._allow_commands_session:
                status = "allowed this session"
            elif t.name != "run_command" and self._allow_writes_session:
                status = "allowed this session"
            else:
                status = "confirm required"
            gate = "只读" if t.read_only else "写/重型"
            lines.append(f"  {t.name} [{gate}] -> {status}")
        if perm.rules:
            lines.append("")
            lines.append("项目 deny 规则:")
            for tool, glob in perm.rules:
                lines.append(f"  deny {tool}: {glob if glob is not None else '*'}")
        return "\n".join(lines)

    def _permission_explain_text(self, tool_name: str, value: str = "") -> str:
        from src.agents.permissions import load_permissions, primary_arg_keys
        agent = self.agent or self._build_main_agent()
        tool = agent.tools.get(tool_name)
        if tool is None:
            names = ", ".join(sorted(agent.tools)[:20])
            return f"未知工具 {tool_name}。可用工具示例: {names}"
        perm = load_permissions(self.repo_root)
        keys = primary_arg_keys(tool_name)
        args = {keys[0]: value} if (value and keys) else {}
        deny = perm.denied(tool_name, args)
        matching = perm.rules_for(tool_name)
        lines = [
            f"[b]权限解释: {tool_name}[/b]",
            f"工具类型: {'只读' if tool.read_only else '写/重型'}",
            f"当前模式: {self.mode}",
        ]
        if keys:
            lines.append(f"主参数键: {', '.join(keys)}" + (f"；本次值: {value}" if value else ""))
        if matching:
            lines.append("匹配到项目规则:")
            for _, glob in matching:
                lines.append(f"  deny {tool_name}: {glob if glob is not None else '*'}")
        else:
            lines.append("项目规则: 无针对该工具的 deny")
        if deny:
            lines.append(f"结论: 硬拦截。原因: {deny}")
        elif self.mode == "plan" and not tool.read_only:
            lines.append("结论: 当前 plan 模式不可直接执行；需要切 build，且仍可能要求确认。")
        elif tool.read_only:
            lines.append("结论: 当前模式允许执行；仍会受项目 deny 规则硬拦。")
        elif tool_name == "run_command" and self._allow_commands_session:
            lines.append("结论: build 下本会话已允许命令；危险命令和 deny 规则仍会硬拦。")
        elif tool_name != "run_command" and self._allow_writes_session:
            lines.append("结论: build 下本会话已允许写/重型工具；deny 规则仍会硬拦。")
        else:
            lines.append("结论: build 下可请求执行，但需要人工确认。")
        if matching and not value and any(glob is not None for _, glob in matching):
            lines.append("提示: 该工具有参数 glob 规则；用 /permissions explain <tool> <value> 可判断具体值是否命中。")
        return "\n".join(lines)

    def _cmd_permissions(self, arg: str = "") -> None:
        """/permissions：查看本会话权限状态与 deny 规则；plan/build/reset/deny 可管理权限。"""
        raw = (arg or "").strip()
        low = raw.lower()
        if low in ("plan", "build"):
            self._set_mode(low)
        elif low in ("show --effective", "effective", "show effective"):
            self._emit(self._permission_effective_text())
            return
        elif low.startswith("explain "):
            parts = raw.split(maxsplit=2)
            tool = parts[1].strip() if len(parts) > 1 else ""
            value = parts[2].strip() if len(parts) > 2 else ""
            if not tool:
                self._emit("用法: /permissions explain <tool> [value]")
                return
            self._emit(self._permission_explain_text(tool, value))
            return
        elif low in ("reset", "reset-session", "session-reset"):
            self._allow_writes_session = False
            self._allow_commands_session = False
            self._sync_subtitle()
            self._chrome("[green]已清除本会话始终允许的写/命令权限[/green]")
        elif low.startswith("deny"):
            parts = raw.split(maxsplit=2)
            if len(parts) < 2:
                self._emit("用法: /permissions deny <tool> [glob]")
                return
            tool = parts[1].strip()
            pattern = parts[2].strip().strip('"').strip("'") if len(parts) > 2 else ""
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", tool):
                self._emit("权限规则工具名非法；用法: /permissions deny <tool> [glob]")
                return
            cfg = self._append_permission_deny(tool, pattern)
            shown = f"{tool}: {pattern}" if pattern else tool
            self._chrome(f"[green]已追加 deny 规则：{shown}（{cfg}）[/green]")
        elif raw:
            self._emit("用法: /permissions [show --effective|explain <tool> [value]|plan|build|reset|deny <tool> [glob]]")
            return
        from src.agents.permissions import load_permissions
        agent = self.agent or self._build_main_agent()
        read_tools = [t.name for t in agent._tool_list if t.read_only]
        write_tools = [t.name for t in agent._tool_list if not t.read_only]
        cfg = Path(self.repo_root) / ".vortocode" / "permissions.yaml"
        perm = load_permissions(self.repo_root)
        lines = [
            "[b]工具权限[/b]",
            f"模式: {self.mode}（plan 只允许只读工具；build 可请求写/重型工具）",
            f"本会话始终允许: 写={'yes' if self._allow_writes_session else 'no'} · "
            f"命令={'yes' if self._allow_commands_session else 'no'}",
            f"工具: 只读 {len(read_tools)} 个 · 写/重型 {len(write_tools)} 个",
            "",
            f"项目 deny 规则: {cfg}",
        ]
        if perm.rules:
            for tool, glob in perm.rules:
                pat = glob if glob is not None else "*"
                lines.append(f"  deny {tool}: {pat}")
        else:
            lines.append("  （无 deny 规则；危险命令仍会走内置拦截和人工确认）")
        lines += [
            "",
            "配置示例:",
            "```yaml",
            "deny:",
            "  - web_fetch",
            '  - "run_command: rm *"',
            '  - edit_file: "*/secrets/*"',
            "```",
            "",
            "提示: 选择“本会话始终允许”只影响当前 TUI 会话；项目 deny 规则始终优先硬拦。",
            "排查: /permissions show --effective · /permissions explain <tool> [value]",
        ]
        self._emit("\n".join(lines))

    def _cmd_memory(self, arg: str = "") -> None:
        """/memory：查看/管理项目指令与长期记忆。"""
        raw = (arg or "").strip()
        low = raw.lower()
        if low.startswith("add "):
            content = raw[4:].strip()
            if not content:
                self._emit("用法: /memory add <要跨会话记住的事实/偏好/约定>")
                return
            self.sessions.store.add_memory("__longterm__", "fact", content, importance=0.6)
            self._chrome(f"[green]已加入长期记忆：{content[:80]}[/green]")
            self._emit(self._memory_status_text())
            return
        if low in ("list", "ls"):
            self._emit(self._memory_list_text())
            return
        if low.startswith(("delete ", "del ", "rm ")):
            parts = raw.split(maxsplit=1)
            memory_id = parts[1].strip() if len(parts) > 1 else ""
            if not memory_id:
                self._emit("用法: /memory delete <id>")
                return
            ok = self.sessions.store.delete_memory("__longterm__", memory_id)
            if ok:
                self._chrome(f"[green]已删除长期记忆 {memory_id}[/green]")
            else:
                self._emit(f"未找到长期记忆 id={memory_id}")
            self._emit(self._memory_list_text())
            return
        if low in ("auto on", "auto true", "auto 1"):
            self._auto_memory = True
            self._save_setting("auto_memory", True)
            self._chrome("[green]自动记忆候选提示已开启[/green]")
            self._emit(self._memory_status_text())
            return
        if low in ("auto off", "auto false", "auto 0"):
            self._auto_memory = False
            self._save_setting("auto_memory", False)
            self._chrome("[dim]自动记忆候选提示已关闭[/dim]")
            self._emit(self._memory_status_text())
            return
        if low in ("init", "init project"):
            self._init_memory_file(local=False)
            self._emit(self._memory_status_text())
            return
        if low in ("init local", "local"):
            self._init_memory_file(local=True)
            self._emit(self._memory_status_text())
            return
        if raw:
            self._emit("用法: /memory [list|add <文本>|delete <id>|auto on|auto off|init|init local]")
            return
        self._emit(self._memory_status_text())

    def _init_memory_file(self, *, local: bool) -> Path:
        path = Path(self.repo_root) / (".vortocode/AGENTS.md" if local else "AGENTS.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            scope = "本地私有" if local else "项目共享"
            path.write_text(
                f"# {scope} Agent 指令\n\n"
                "## 项目约定\n"
                "- 使用中文沟通，代码注释遵循现有风格。\n"
                "- 开发前先确认当前模式：plan 只读分析，build 才写入。\n"
                "- 修改后说明验证命令和结果。\n\n"
                "## Review guidelines\n"
                "- 优先指出真实会影响运行、构建或维护的问题。\n"
                "- 结论需要绑定文件、行号或可复现证据。\n",
                encoding="utf-8")
        self.agent = None          # 下一轮重建，重新加载项目指令
        self._chrome(f"[green]项目记忆文件已就绪：{path}[/green]")
        return path

    def _memory_status_text(self) -> str:
        from src.agents.project import find_instructions_file
        instr = find_instructions_file(self.repo_root)
        rows = self.sessions.store.get_memories("__longterm__")
        lines = ["[b]项目记忆[/b]"]
        if instr:
            try:
                preview = instr.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[:5]
            except Exception:  # noqa: BLE001
                preview = []
            lines.append(f"项目指令: {instr}")
            if preview:
                lines.append("  " + "\n  ".join(preview))
        else:
            lines.append("项目指令: （未找到 AGENTS.md / CLAUDE.md / VORTO.md / .vortocode/AGENTS.md）")
            lines.append("  用 /memory init 创建共享 AGENTS.md；用 /memory init local 创建本地私有指令。")
        lines.append("")
        lines.append(f"长期记忆: {len(rows)} 条 · 自动候选提示: {'on' if self._auto_memory else 'off'}")
        for r in rows[:8]:
            lines.append(f"  - {r.get('id')} · {str(r.get('content', ''))[:120]}")
        if len(rows) > 8:
            lines.append(f"  ... 还有 {len(rows) - 8} 条")
        lines.append("")
        lines.append("用法: /memory list · /memory add <文本> · /memory delete <id> · /memory auto on/off · /memory init")
        return "\n".join(lines)

    def _memory_list_text(self) -> str:
        rows = self.sessions.store.get_memories("__longterm__")
        if not rows:
            return "长期记忆为空。用 /memory add <文本> 添加。"
        lines = [f"[b]长期记忆[/b]（{len(rows)} 条）"]
        for r in rows:
            created = str(r.get("created_at") or "")[:19]
            content = str(r.get("content") or "")
            lines.append(f"  {r.get('id')} · {r.get('type', 'fact')} · {created} · {content}")
        lines.append("\n用 /memory delete <id> 删除。")
        return "\n".join(lines)

    def _auto_memory_candidate(self, text: str) -> str:
        """从用户明确表达的长期偏好/项目约定里提取候选；保守规则，不调用 LLM。"""
        raw = self._summary_text(text, 220).strip()
        if not raw or raw.startswith("/"):
            return ""
        explicit = ("记住", "帮我记", "长期记忆")
        preference = ("以后", "后续", "默认", "习惯", "偏好", "项目约定", "统一", "不要再")
        if not any(w in raw for w in explicit + preference):
            return ""
        if not any(w in raw for w in explicit) and any(w in raw for w in ("吗", "？", "?", "为什么", "怎么")):
            return ""
        content = raw
        for marker in ("记住：", "记住:", "帮我记住：", "帮我记住:", "长期记忆：", "长期记忆:"):
            if marker in content:
                content = content.split(marker, 1)[1].strip()
                break
        content = content.strip(" ，,。.")
        if len(content) < 6:
            return ""
        if any(w in content for w in ("pytest", "ruff", "pnpm", "npm", "yarn", "uv", "cargo", "swift test")):
            prefix = "项目偏好"
        elif any(w in content for w in ("项目", "约定", "默认", "统一")):
            prefix = "项目约定"
        else:
            prefix = "用户偏好"
        return f"{prefix}：{content[:180]}"

    def _memory_exists(self, content: str) -> bool:
        try:
            rows = self.sessions.store.get_memories("__longterm__")
        except Exception:  # noqa: BLE001
            return False
        normalized = " ".join(content.split())
        return any(" ".join(str(r.get("content") or "").split()) == normalized for r in rows)

    def _maybe_offer_auto_memory(self) -> None:
        if not self._auto_memory or self._inline_confirm_active():
            return
        candidate = self._auto_memory_candidate(self._session_last_user)
        if not candidate or candidate == self._last_memory_candidate or self._memory_exists(candidate):
            return
        self._last_memory_candidate = candidate

        def _done(ok: bool | None) -> None:
            if not ok:
                self._chrome("[dim]未保存自动记忆候选[/dim]")
                return
            try:
                mid = self.sessions.store.add_memory(
                    "__longterm__", "fact", candidate, importance=0.65,
                    metadata={"source": "auto_candidate", "session_id": self.session_id})
            except Exception as e:  # noqa: BLE001
                self._emit(f"保存自动记忆失败: {e}")
                return
            self._chrome(f"[green]已保存长期记忆 {mid}[/green]")

        self._begin_inline_confirm(
            "检测到可能值得跨会话记住的项目偏好/约定：\n"
            f"{candidate}\n"
            "保存到长期记忆？",
            scope="memory",
            callback=_done,
        )

    def _cmd_tasks(self, arg: str = "") -> None:
        """/tasks：列出 dev_auto 持久化计划；show 看详情；resume 续跑。"""
        raw = (arg or "").strip()
        from src.agents.dev_plan import format_plan_detail, format_plan_list, list_plans, load_plan
        if not raw or raw.lower() in {"list", "ls"}:
            self._emit(format_plan_list(list_plans(self.repo_root)))
            return
        parts = raw.split(maxsplit=1)
        action = parts[0].lower()
        if action in {"show", "detail", "details", "resume", "continue"}:
            if len(parts) < 2 or not parts[1].strip():
                self._emit("用法: /tasks show <plan_id> 或 /tasks resume <plan_id>")
                return
            pid = parts[1].strip()
        else:
            pid = raw
        plan = load_plan(self.repo_root, pid)
        if plan is None:
            self._emit(f"找不到 dev 计划 {pid}。用 /tasks 查看最近计划。")
            return
        if action in {"resume", "continue"}:
            prompt = (
                f"续跑 dev 计划 {plan.plan_id}？\n"
                f"任务: {plan.task[:120]}\n"
                f"分支: {plan.branch} · 状态: {plan.status}\n"
                "这会切到 build，并让主 agent 调用 dev_resume。"
            )

            def _done(ok: bool | None) -> None:
                if not ok:
                    self._emit("已取消续跑计划。")
                    return
                if self.mode != "build":
                    self.mode = "build"
                    self._sync_subtitle()
                    self._chrome("[green]→ 已切到 build 模式[/green]")
                    self._record_mode_change()
                self._continue_text_route(
                    f"请续跑 dev 计划 plan_id={plan.plan_id}，调用 dev_resume 工具继续未完成任务。")

            self._begin_inline_confirm(prompt, scope="writes", callback=_done)
            return
        self._emit(format_plan_detail(plan))

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

    def _cmd_diff(self, arg: str = "") -> None:
        """/diff：把工作区改动着色渲染出来；支持 stat/hunks/cached/路径过滤。"""
        import shlex
        import subprocess
        try:
            tokens = shlex.split(arg or "")
        except ValueError as e:
            self._emit(f"用法: /diff [stat|hunks|cached|staged] [路径...]（参数解析失败: {e}）")
            return
        cached = False
        stat = False
        hunks = False
        paths: list[str] = []
        for tok in tokens:
            low = tok.lower()
            if low in {"cached", "staged", "--cached", "--staged"}:
                cached = True
            elif low in {"stat", "--stat"}:
                stat = True
            elif low in {"hunk", "hunks", "--hunks"}:
                hunks = True
            elif tok.startswith("-"):
                self._emit("用法: /diff [stat|hunks|cached|staged] [路径...]（不透传其它 git 参数）")
                return
            else:
                paths.append(tok)
        if stat and hunks:
            self._emit("用法: /diff stat 或 /diff hunks，二者不要混用")
            return
        if hunks:
            from src.agents.git_workflow import diff_hunks_for_review, format_diff_hunks
            self._emit(format_diff_hunks(diff_hunks_for_review(self.repo_root, cached=cached, paths=paths)))
            return
        cmd = ["git", "diff"]
        if cached:
            cmd.append("--cached")
        if stat:
            cmd.append("--stat")
        if paths:
            cmd.append("--")
            cmd.extend(paths)
        try:
            r = subprocess.run(cmd, cwd=self.repo_root,
                               capture_output=True, text=True, timeout=15)
        except Exception as e:  # noqa: BLE001
            self._emit(f"git diff 失败: {e}（不是 git 仓库？）")
            return
        if r.returncode != 0:
            self._emit((r.stderr or r.stdout or "git diff 失败").strip())
            return
        diff = r.stdout or ""
        if not diff.strip():
            label = " ".join(cmd)
            self._emit(f"({label} 为空)")
            return
        label = " ".join(cmd)
        self._chrome(f"[dim]工作区改动（{label}）:[/dim]")
        if stat:
            self._emit(diff.rstrip())
            return
        self._render_diff_text(diff, max_lines=400)

    def _cmd_changes(self, arg: str = "") -> None:
        """/changes：提交前变更审查摘要；支持 cached/staged 和路径过滤。"""
        import shlex
        try:
            tokens = shlex.split(arg or "")
        except ValueError as e:
            self._emit(f"用法: /changes [cached|staged] [路径...]（参数解析失败: {e}）")
            return
        cached = False
        paths: list[str] = []
        for tok in tokens:
            low = tok.lower()
            if low in {"cached", "staged", "--cached", "--staged"}:
                cached = True
            elif tok.startswith("-"):
                self._emit("用法: /changes [cached|staged] [路径...]（不透传其它 git 参数）")
                return
            else:
                paths.append(tok)
        from src.agents.git_workflow import change_review, format_change_review
        self._emit(format_change_review(change_review(self.repo_root, cached=cached, paths=paths)))

    def _cmd_review(self, arg: str = "") -> None:
        """/review [--fix] [hunk H1] [cached] [路径...]：LLM diff review，只报 P0/P1。"""
        import shlex
        try:
            tokens = shlex.split(arg or "")
        except ValueError as e:
            self._emit(f"用法: /review [--fix] [hunk H1] [cached|staged] [路径...]（参数解析失败: {e}）")
            return
        cached = False
        fix = False
        hunk_id = ""
        paths: list[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            low = tok.lower()
            if low in {"cached", "staged", "--cached", "--staged"}:
                cached = True
            elif low in {"--fix", "fix"}:
                fix = True
            elif low in {"hunk", "--hunk"}:
                i += 1
                if i >= len(tokens) or tokens[i].startswith("-"):
                    self._emit("用法: /review [--fix] [hunk H1] [cached|staged] [路径...]")
                    return
                hunk_id = tokens[i].strip().upper()
            elif tok.startswith("-"):
                self._emit("用法: /review [--fix] [hunk H1] [cached|staged] [路径...]（不透传其它参数）")
                return
            else:
                paths.append(tok)
            i += 1

        async def _run():
            try:
                result = await self._run_diff_review(cached=cached, paths=paths, hunk_id=hunk_id)
            except Exception as e:  # noqa: BLE001
                self._emit(f"review 出错: {e}")
                return
            self._emit(result)
            if fix:
                self._offer_review_fix(result, cached=cached, paths=paths, hunk_id=hunk_id)

        self.run_worker(_run(), exclusive=True, group="review")

    def _offer_review_fix(self, review_text: str, *, cached: bool = False,
                          paths: list[str] | None = None, hunk_id: str = "") -> None:
        text = str(review_text or "").strip()
        low = text.lower()
        no_findings = "未发现 p0/p1" in low or "no p0/p1" in low
        unavailable = "review 失败" in text or "OPENAI_API_KEY" in text
        if no_findings or unavailable:
            self._emit("review 未发现可自动修复的 P0/P1，已保持只读。")
            return
        scope = "已 staged diff" if cached else "工作区 diff"
        if hunk_id:
            scope += f" · {hunk_id}"
        path_hint = f"；路径: {', '.join(paths)}" if paths else ""
        prompt = (
            f"按 /review 发现的问题自动修复？范围: {scope}{path_hint}。\n"
            "这会切到 build 模式，并让主 agent 只修 P0/P1，不处理风格建议。"
        )

        def _done(ok: bool | None) -> None:
            if not ok:
                self._emit("已取消 review 修复。")
                return
            self._set_mode("build")
            excerpt = text[:4000]
            self._continue_text_route(
                "请根据刚才 /review 的 P0/P1 审查结论修复当前 diff。"
                "要求：只改必要处，修完后运行最相关验证；不要处理风格、命名或微优化。\n\n"
                f"范围: {scope}{path_hint}\n\n审查结论:\n{excerpt}"
            )

        self._begin_inline_confirm(prompt, scope="writes", callback=_done)

    async def _run_diff_review(self, *, cached: bool = False, paths: list[str] | None = None,
                               hunk_id: str = "") -> str:
        import os
        if not os.getenv("OPENAI_API_KEY"):
            return "配置 OPENAI_API_KEY 后可用 /review 做 LLM diff 审查；无需 key 可先用 /changes 和 /preflight。"
        from src.agents.git_workflow import diff_for_review
        payload = diff_for_review(self.repo_root, cached=cached, paths=paths or [], hunk_id=hunk_id)
        if not payload.get("ok"):
            return f"review 失败: {payload.get('error', '')}".rstrip()
        diff = str(payload.get("diff") or "").strip()
        if not diff:
            return "review 失败: diff 为空（未跟踪文件请先 git add，或用 /changes 查看范围）。"
        from src.agents.main_agent import MainAgent
        from src.agents.review import load_review_guidelines
        guidelines = load_review_guidelines(self.repo_root)
        extra = (
            "你是一个严格的代码审查员。只审查用户给出的 diff。\n"
            "只报告 P0/P1：P0=会导致崩溃、数据丢失、安全漏洞、明显错误结果；"
            "P1=重要正确性问题。不要报告风格、命名、可读性、微优化。\n"
            "每条问题必须包含：severity、文件/行号或 hunk、问题、为什么会发生、建议修复。"
            "没有足够证据的猜测不要报。若没有 P0/P1，直接说“未发现 P0/P1”。"
        )
        if guidelines:
            extra += "\n\n项目 Review guidelines:\n" + guidelines
        agent = MainAgent([], max_steps=2, extra_system=extra, native=False)
        scope = "staged" if cached else "workspace"
        if hunk_id:
            scope += f" hunk {hunk_id.upper()}"
        prompt = (
            f"请审查下面 {scope} diff，只输出审查结论。"
            "优先列 Findings；没有发现就说未发现 P0/P1。\n\n"
            f"```diff\n{diff}\n```"
        )
        return await agent.run_turn(prompt, mode="plan")

    def _cmd_verify(self, arg: str = "") -> None:
        """/verify [selector|--changed] 或 /verify run <命令>。"""
        import shlex
        raw_arg = arg or ""
        try:
            tokens = shlex.split(raw_arg)
        except ValueError as e:
            self._emit(f"用法: /verify [selector|--changed] [cached] 或 /verify run <命令>（参数解析失败: {e}）")
            return
        if tokens and tokens[0].lower() in {"profiles", "profile", "list"}:
            if len(tokens) == 1 or tokens[0].lower() in {"profiles", "list"}:
                from src.agents.verify_profiles import format_verify_profiles, load_verify_profiles
                self._emit(format_verify_profiles(load_verify_profiles(self.repo_root)))
                return
            self._cmd_verify_profile(tokens[1])
            return
        if len(tokens) == 1 and not tokens[0].startswith("-"):
            from src.agents.verify_profiles import resolve_verify_profile
            resolved = resolve_verify_profile(self.repo_root, tokens[0])
            if resolved.get("ok"):
                self._cmd_verify_profile(str(resolved.get("name") or tokens[0]))
                return
        if tokens and tokens[0].lower() in {"run", "runtime", "cmd", "command"}:
            parts = raw_arg.strip().split(maxsplit=1)
            cmd = parts[1].strip() if len(parts) > 1 else ""
            self._cmd_verify_run(cmd)
            return
        changed = False
        cached = False
        selector_parts: list[str] = []
        for tok in tokens:
            low = tok.lower()
            if low in {"--changed", "changed"}:
                changed = True
            elif low in {"cached", "staged", "--cached", "--staged"}:
                cached = True
            elif tok.startswith("-"):
                self._emit("用法: /verify [selector|--changed] [cached] 或 /verify run <命令>（不透传其它测试参数）")
                return
            else:
                selector_parts.append(tok)
        selector = " ".join(selector_parts).strip()
        if changed and selector:
            self._emit("用法: /verify [selector] 或 /verify --changed [cached]，二者不要混用")
            return
        from src.agents.test_detect import detect_test_cmd, is_pytest_cmd
        note = ""
        if changed:
            from src.agents.git_workflow import changed_test_selection
            sel = changed_test_selection(self.repo_root, cached=cached)
            if not sel.get("ok"):
                self._emit(f"改动测试选择失败: {sel.get('error', '')}")
                return
            if not sel.get("changed_paths"):
                self._emit(sel.get("reason") or "没有改动可验证。")
                return
            base_cmd = detect_test_cmd(self.repo_root)
            selectors = list(sel.get("selectors") or [])
            if selectors and is_pytest_cmd(base_cmd):
                import sys
                cmd = [sys.executable, "-m", "pytest", "-q", *selectors]
            else:
                cmd = base_cmd
            note = str(sel.get("reason") or "")
        else:
            cmd = detect_test_cmd(self.repo_root, selector or None)
        cmd_text = " ".join(cmd)

        async def _run():
            detail = f"\n  {note}" if note else ""
            if not await self._confirm_command(
                    "运行仓库测试验证？\n"
                    f"  $ {cmd_text}\n"
                    f"测试可能写入缓存或耗时较久。{detail}"):
                self._emit("已取消验证。")
                return
            self._chrome(f"[dim]$ {cmd_text}[/dim]")
            from src.agents.worktree import run_tests
            res = await asyncio.to_thread(run_tests, self.repo_root, cmd)
            ok = bool(res.get("ok"))
            status = "验证通过 ✓" if ok else "验证失败 ✗"
            color = self._tc("text-success", "#7fce9a") if ok else self._tc("text-error", "#f08a8a")
            self._chrome(f"[{color}]{status}[/][dim]（{res.get('cmd') or cmd_text}）[/dim]")
            out = str(res.get("output") or "").strip()
            if out:
                self._emit(f"{status}（{res.get('cmd') or cmd_text}）\n输出尾部:\n{out[-3000:]}")
            else:
                self._emit(f"{status}（{res.get('cmd') or cmd_text}）")

        self.run_worker(_run(), exclusive=True, group="verify")

    def _cmd_verify_profile(self, name: str) -> None:
        from src.agents.verify_profiles import format_verify_profiles, resolve_verify_profile
        resolved = resolve_verify_profile(self.repo_root, name)
        if not resolved.get("ok"):
            self._emit(str(resolved.get("error") or "verify profile 加载失败"))
            profiles = resolved.get("profiles") or {}
            if profiles:
                self._emit(format_verify_profiles({"ok": True, "profiles": profiles}))
            return
        profile = resolved.get("profile") or {}
        desc = str(profile.get("description") or "").strip()
        label = f"profile {resolved.get('name')}" + (f" · {desc}" if desc else "")
        self._cmd_verify_run(str(profile.get("cmd") or ""), label=label)

    def _cmd_verify_run(self, cmd: str, *, label: str = "runtime 验证命令") -> None:
        """Run a user-supplied runtime/smoke command with existing command gates."""
        cmd = (cmd or "").strip()
        if not cmd:
            self._emit("用法: /verify run <命令>")
            return
        from src.agents.shell import is_dangerous
        danger = is_dangerous(cmd)
        if danger:
            self._emit(f"拒绝执行高危验证命令: {danger}")
            return

        async def _run():
            if not await self._confirm_command(
                    f"运行 {label}？\n"
                    f"  $ {cmd}\n"
                    "适合 smoke test、启动检查、端到端脚本；请确认命令不会做外向或破坏性操作。"):
                self._emit("已取消 runtime 验证。")
                return
            self._chrome(f"[dim]$ {cmd}[/dim]")
            from src.agents.shell import run_command
            res = await asyncio.to_thread(run_command, self.repo_root, cmd)
            ok = bool(res.get("ok"))
            status = "runtime 验证通过 ✓" if ok else "runtime 验证失败 ✗"
            color = self._tc("text-success", "#7fce9a") if ok else self._tc("text-error", "#f08a8a")
            self._chrome(f"[{color}]{status}[/][dim]（{cmd}）[/dim]")
            out = str(res.get("output") or "").strip()
            if out:
                self._emit(f"{status}（{cmd}）\n输出尾部:\n{out[-3000:]}")
            else:
                self._emit(f"{status}（{cmd}）")

        self.run_worker(_run(), exclusive=True, group="verify")

    def _cmd_preflight(self, arg: str = "") -> None:
        """/preflight [cached]：提交/开 PR 前只读检查。"""
        import shlex
        try:
            tokens = shlex.split(arg or "")
        except ValueError as e:
            self._emit(f"用法: /preflight [cached|staged]（参数解析失败: {e}）")
            return
        cached = False
        for tok in tokens:
            low = tok.lower()
            if low in {"cached", "staged", "--cached", "--staged"}:
                cached = True
            else:
                self._emit("用法: /preflight [cached|staged]")
                return
        from src.agents.git_workflow import format_preflight_report, preflight_report
        self._emit(format_preflight_report(preflight_report(self.repo_root, cached=cached)))

    def _cmd_git(self, arg: str = "") -> None:
        """/git：查看当前分支、改动文件、staged/unstaged diffstat。"""
        if (arg or "").strip():
            self._emit("用法: /git")
            return
        from src.agents.git_workflow import format_status_summary, status_summary
        self._emit(format_status_summary(status_summary(self.repo_root)))

    def _cmd_commit(self, arg: str = "") -> None:
        """/commit <msg>：提交 staged 改动；/commit all <msg> 先 git add -A。"""
        import shlex
        try:
            tokens = shlex.split(arg or "")
        except ValueError as e:
            self._emit(f"用法: /commit <message>|suggest 或 /commit all <message|--suggest>（参数解析失败: {e}）")
            return
        if not tokens:
            self._emit("用法: /commit <message>|suggest 或 /commit all <message|--suggest>")
            return
        stage_all = tokens[0].lower() == "all"
        raw_msg_tokens = tokens[1:] if stage_all else tokens
        suggest = any(t.lower() in {"suggest", "--suggest"} for t in raw_msg_tokens)
        msg_tokens = [t for t in raw_msg_tokens if t.lower() not in {"suggest", "--suggest"}]
        message = " ".join(msg_tokens).strip()
        if suggest:
            from src.agents.git_workflow import suggest_commit_message
            suggested = suggest_commit_message(self.repo_root, stage_all=stage_all)
            if not suggested.get("ok"):
                self._emit(f"生成提交信息失败: {suggested.get('error', '')}")
                return
            message = str(suggested.get("message") or "").strip()
        if not message:
            self._emit("用法: /commit <message>|suggest 或 /commit all <message|--suggest>")
            return

        async def _run():
            from src.agents.git_workflow import commit_changes, has_any_changes, has_staged_changes
            if stage_all:
                if not has_any_changes(self.repo_root):
                    self._emit("没有工作区改动可提交。")
                    return
            elif not has_staged_changes(self.repo_root):
                self._emit("没有 staged 改动可提交。用 /commit all <message> 可先 git add -A。")
                return
            if self.mode != "build":
                prompt = "当前是 plan 模式。切到 build 并执行本地 git commit？"
            else:
                prompt = "执行本地 git commit？"
            detail = f"\n  message: {message}"
            if suggest:
                detail += "\n  message 由当前改动自动生成"
            if stage_all:
                detail += "\n  会先执行: git add -A"
            if not await self._confirm_command(prompt + detail):
                self._emit("已取消 commit。")
                return
            if self.mode != "build":
                self.mode = "build"
                self._sync_subtitle()
                self._chrome("[green]→ 已切到 build 模式[/green]")
                self._record_mode_change()
            result = await asyncio.to_thread(commit_changes, self.repo_root, message, stage_all=stage_all)
            if not result.get("ok"):
                self._emit(f"commit 失败: {result.get('error', '')}")
                return
            self._audit_event("commit", {"sha": result.get("sha"), "stage_all": stage_all, "message": message})
            self._emit(f"已提交 {result.get('sha')}: {message}")
            self._render_statusbar()

        self.run_worker(_run(), exclusive=True, group="git")

    def _parse_pr_args(self, arg: str) -> dict:
        import shlex
        try:
            tokens = shlex.split(arg or "")
        except ValueError as e:
            return {"ok": False, "error": f"参数解析失败: {e}"}
        preview = False
        draft = False
        base = "main"
        title_parts: list[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            low = tok.lower()
            if low == "preview":
                preview = True
            elif low == "draft":
                draft = True
            elif low == "base":
                if i + 1 >= len(tokens):
                    return {"ok": False, "error": "base 需要一个引用，如 /pr base main"}
                base = tokens[i + 1]
                i += 1
            elif tok.startswith("-"):
                return {"ok": False, "error": "用法: /pr [preview|draft] [base <ref>] [title]"}
            else:
                title_parts.append(tok)
            i += 1
        return {"ok": True, "preview": preview, "draft": draft, "base": base, "title": " ".join(title_parts).strip()}

    def _cmd_pr(self, arg: str = "") -> None:
        """/pr：预览或创建当前分支 PR。外向操作，创建前必须确认。"""
        opts = self._parse_pr_args(arg)
        if not opts.get("ok"):
            self._emit(opts.get("error", "用法: /pr [preview|draft] [base <ref>] [title]"))
            return
        from src.agents.git_workflow import format_pr_preview, pr_preview
        preview = pr_preview(self.repo_root, base=opts["base"], title=opts["title"])
        if opts["preview"] or not preview.get("ok"):
            self._emit(format_pr_preview(preview))
            return

        async def _run():
            text = format_pr_preview(preview)
            if opts.get("draft"):
                text += "\n\n将创建 draft PR。"
            if not await self._confirm_outward(text + "\n\n确认 push 当前分支并创建 PR？"):
                self._emit("已取消开 PR。")
                return
            from src.agents.vcs import push_and_open_pr
            res = await asyncio.to_thread(
                push_and_open_pr,
                self.repo_root,
                preview["branch"],
                preview["title"],
                preview["body"],
                preview["base"],
                "origin",
                bool(opts.get("draft")),
            )
            self._audit_event("open_pr", {
                "branch": preview.get("branch"),
                "base": preview.get("base"),
                "title": preview.get("title"),
                "draft": bool(opts.get("draft")),
                "ok": bool(res.get("ok")),
                "url": res.get("url", ""),
            })
            if res.get("ok"):
                self._emit(f"已创建 PR: {res.get('url')}")
            elif res.get("pushed"):
                self._emit(f"已 push {preview['branch']}，但开 PR 失败: {res.get('error', '')}")
            else:
                self._emit(f"开 PR 失败: {res.get('error', '')}")

        self.run_worker(_run(), exclusive=True, group="git")

    def _format_pr_feedback(self, fb: dict, ref: str) -> str:
        if not fb.get("ok"):
            return f"PR 反馈读取失败（{ref}）: {fb.get('error', '')}"
        pr = fb.get("pr") or ref
        branch = fb.get("branch") or "未知分支"
        comments = fb.get("comments") or []
        checks = fb.get("failing_checks") or []
        lines = [f"PR #{pr} · {branch}", f"待处理 review 评论: {len(comments)}", f"失败检查: {len(checks)}"]
        if checks:
            lines.append("")
            lines.append("失败检查:")
            for ck in checks[:10]:
                link = f" — {ck.get('link')}" if ck.get("link") else ""
                lines.append(f"- {ck.get('name') or 'check'}{link}")
            if len(checks) > 10:
                lines.append(f"- ... 还有 {len(checks) - 10} 个")
        if comments:
            lines.append("")
            lines.append("Review 评论:")
            for c in comments[:20]:
                if c.get("path") and c.get("line"):
                    loc = f"{c.get('path')}:{c.get('line')}"
                else:
                    loc = str(c.get("path") or "PR")
                author = c.get("author") or "?"
                body = self._summary_text(str(c.get("body") or ""), 180)
                lines.append(f"- [{author}] {loc} {body}")
            if len(comments) > 20:
                lines.append(f"- ... 还有 {len(comments) - 20} 条")
        if not comments and not checks:
            lines.append("")
            lines.append("没有待处理 review 评论，CI 也没有失败检查。")
        return "\n".join(lines)

    def _cmd_pr_check(self, arg: str = "") -> None:
        """/pr-check <ref>：读取 PR review 评论 + 失败 CI。只读命令。"""
        ref = (arg or "").strip()
        if not ref:
            self._emit("用法: /pr-check <PR号或分支名>")
            return

        async def _run():
            from src.agents.vcs import pr_feedback
            fb = await asyncio.to_thread(pr_feedback, self.repo_root, ref)
            self._emit(self._format_pr_feedback(fb, ref))

        self.run_worker(_run(), exclusive=True, group="git")

    def _cmd_pr_fix(self, arg: str = "") -> None:
        """/pr-fix <ref>：确认后切 build，并让主 agent 调 pr_fix 工具。"""
        ref = (arg or "").strip()
        if not ref:
            self._emit("用法: /pr-fix <PR号或vorto/*分支名>")
            return

        def _after_confirm(ok: bool | None) -> None:
            if not ok:
                self._emit("已取消 PR 反馈修复。")
                return
            self._set_mode("build")
            self._continue_text_route(
                f"请读取并修复 PR {ref} 的 review/CI 反馈；调用 pr_fix 工具，参数 pr={ref}。"
            )

        self._begin_inline_confirm(
            f"按 PR {ref} 的 review/CI 反馈自动修复？会切到 build 模式，并由 pr_fix 在 vorto/* 分支上改动、自测、再确认 push。",
            scope="writes",
            callback=_after_confirm,
        )

    def _user_commands(self) -> dict:
        """惰性加载并缓存 .vortocode/commands 下的用户自定义命令（/commands reload 重扫）。"""
        if self._user_cmds is None:
            from src.agents.user_commands import load_commands
            self._user_cmds = load_commands(self.repo_root)
        return self._user_cmds

    def _run_user_command(self, name: str, arg: str) -> None:
        """展开某条用户自定义命令的模板（替换占位符），作为一轮输入交给主 agent。"""
        if self._busy:
            self._chrome("[yellow]正在处理上一条，Esc 取消或稍候[/yellow]")
            return
        from src.agents.user_commands import expand_command
        uc = self._user_commands().get(name)
        if uc is None:
            self._chrome(f"[red]未知命令 /{name}[/red]")
            return
        if getattr(uc, "mode", "") and uc.mode != self.mode:
            self._set_mode(uc.mode)
        self._chrome(f"[dim]▶ /{name}[/dim] [dim italic]{self._user_command_label(uc)}[/dim italic]")
        self._route(expand_command(uc.template, arg))

    def _cmd_commands(self, arg: str = "") -> None:
        """/commands：列出 .vortocode/commands 下的自定义命令；/commands reload 重扫目录。"""
        if (arg or "").strip().lower() == "reload":
            self._user_cmds = None
        cmds = self._user_commands()
        if not cmds:
            self._emit("没有自定义命令。在 `.vortocode/commands/<名>.md` 写提示模板即可用 `/<名>` 调起"
                       "（支持 $ARGUMENTS / $1 占位符；frontmatter 可写 description/mode/argument-hint/model）。")
            return
        lines = ["[b]自定义命令[/b]（.vortocode/commands）:"]
        for n, uc in sorted(cmds.items()):
            lines.append(f"  [b]/{n}[/b] — {self._user_command_label(uc)}")
        lines.append("[dim]在文件里用 $ARGUMENTS / $1 接收参数；frontmatter 可写 mode: plan/build；/commands reload 重扫。[/dim]")
        self._chrome("\n".join(lines))

    def _cmd_hooks(self) -> None:
        """/hooks：列出 .vortocode/hooks.yaml 配置的工具生命周期钩子（事件 / 工具 matcher）。"""
        from pathlib import Path
        cfg = Path(self.repo_root) / ".vortocode" / "hooks.yaml"
        hs = self._load_hook_system()
        if hs is None:
            self._emit(
                "没有 hooks。在 `.vortocode/hooks.yaml` 配置工具生命周期钩子，例如 edit_file 后自动格式化：\n"
                "```yaml\nhooks:\n  - name: fmt\n    type: command\n    event_types: [post_tool_use]\n"
                "    matcher: edit_file|write_file   # 只在这些工具后触发\n    shell: true\n    command: ruff format .\n```")
            return
        hooks = [h for h in hs.list_hooks() if h.__class__.__name__ != "AuditLogHook"]
        if not hooks:
            self._emit(f"{cfg} 里没有可用钩子（或都解析失败）。")
            return
        lines = [f"[b]工具生命周期钩子[/b]（{cfg}）:"]
        for h in hooks:
            evs = "/".join(e.value for e in h.event_types)
            m = f" · 仅工具 [b]{h.matcher}[/b]" if getattr(h, "matcher", None) else ""
            state = "" if h.enabled else " [dim](禁用)[/dim]"
            lines.append(f"  [b]{h.name}[/b] [{self._tc('text-primary', '#8ab4f8')}]{evs}[/]{m}{state}")
        lines.append("[dim]pre_tool_use 可拦工具（should_stop）、post_tool_use 可附信息；matcher 是工具名正则。[/dim]")
        self._chrome("\n".join(lines))

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
        """把已连接的 MCP server 工具包成主 agent 的 Tool（复用共享 wrap_mcp_manager，与 Web/CLI 同源）。

        命名 mcp__<server>__<tool> 防冲突；外部工具一律 build 门控（read_only=False，人在关口）。
        """
        from src.agents.mcp_tools import wrap_mcp_manager
        return wrap_mcp_manager(self._mcp)

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
        buf: list[str] = []
        preview = self._make_stream_preview(label=cfg.name)

        def on_token(tok: str) -> None:
            buf.append(tok)
            preview("".join(buf))

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

    # ---------------------------------------------------------------- 会话
    def _cmd_sessions(self, arg: str = "") -> None:
        """/sessions：会话选择器；rename/delete 管理历史会话。"""
        raw = (arg or "").strip()
        low = raw.lower()
        if low.startswith("rename "):
            parts = raw.split(maxsplit=2)
            if len(parts) < 3 or not parts[1].strip() or not parts[2].strip():
                self._emit("用法: /sessions rename <id> <name>")
                return
            sid, name = parts[1].strip(), parts[2].strip()
            if not self.sessions.store.get_session(sid):
                self._emit(f"没有会话 {sid}")
                return
            self.sessions.store.update_session(sid, name=name)
            self._chrome(f"[green]已重命名会话 {sid} → {name}[/green]")
            self._emit(self._session_manage_list_text())
            return
        if low.startswith(("delete ", "del ", "rm ")):
            parts = raw.split(maxsplit=1)
            sid = parts[1].strip() if len(parts) > 1 else ""
            if not sid:
                self._emit("用法: /sessions delete <id>")
                return
            row = self.sessions.store.get_session(sid)
            if not row:
                self._emit(f"没有会话 {sid}")
                return

            def _done(ok: bool | None) -> None:
                if not ok:
                    self._emit("已取消删除会话。")
                    return
                self.sessions.store.delete_session(sid)
                if sid == self.session_id:
                    self.session_id = self.sessions.start_session()
                    self.agent = None
                    self.transcript.clear()
                    try:
                        self.query_one("#log", RichLog).clear()
                    except Exception:  # noqa: BLE001
                        pass
                    self._chrome(f"[yellow]已删除当前会话，并新建会话 {self.session_id}[/yellow]")
                else:
                    self._chrome(f"[green]已删除会话 {sid}[/green]")
                self._emit(self._session_manage_list_text())

            self._begin_inline_confirm(
                f"删除会话 {sid}（{row.get('name') or sid}）及其消息、任务、编辑和记忆？\n"
                "此操作不可撤销。",
                scope="sessions",
                callback=_done,
            )
            return
        if raw and low not in {"list", "ls"}:
            self._emit("用法: /sessions [list|rename <id> <name>|delete <id>]")
            return
        rows = self.sessions.list_recent_sessions(20)
        if not rows:
            self._emit("(暂无历史会话)")
            return
        items = []
        for r in rows:
            summ = self.sessions.store.get_session_summary(r["id"])
            mark = "  ← 当前" if r["id"] == self.session_id else ""
            label = self._session_picker_label(r)
            items.append((r["id"],
                          f"{r['id']}  {label} · 消息 {summ.get('messages', 0)} · "
                          f"{(r.get('updated_at') or '')[:19]}{mark}"))

        def _done(sid) -> None:
            if sid and sid != self.session_id:
                self._cmd_resume(sid)

        self.push_screen(ListPicker("历史会话 · 回车恢复", items, initial=self.session_id), _done)

    def _session_manage_list_text(self) -> str:
        rows = self.sessions.list_recent_sessions(20)
        if not rows:
            return "(暂无历史会话)"
        lines = [f"历史会话（{len(rows)} 个，最近 20 个）:"]
        for r in rows:
            label = self._session_picker_label(r)
            mark = " ← 当前" if r["id"] == self.session_id else ""
            lines.append(f"  {r['id']} · {label} · {(r.get('updated_at') or '')[:19]}{mark}")
        lines.append("\n用 /sessions rename <id> <name> 重命名；/sessions delete <id> 删除。")
        return "\n".join(lines)

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
        self._session_last_user = ""
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
        session_summary = ""
        try:
            row = self.sessions.store.get_session(sid) or {}
            session_md = self._session_metadata(row)
            session_summary = str(session_md.get("summary") or "")
        except Exception:  # noqa: BLE001
            row = {}
            session_summary = ""
        self._emit(self._resume_context_text(row))
        self._restore_agent_history(last_snapshot, fallback_summary=session_summary)

    def _restore_agent_history(self, snapshot, *, fallback_summary: str = "") -> None:
        """从快照重建 agent 历史，让 /resume 后主 agent 记得之前聊了什么。"""
        if not snapshot:
            if fallback_summary:
                self.agent = self._build_main_agent()
                self.agent._summary = fallback_summary
                self._chrome("[dim]↻ 已恢复会话摘要（可继续接上历史话题）[/dim]")
                self._render_statusbar()
            return
        try:
            raw = json.loads(snapshot)
        except Exception:  # noqa: BLE001
            return
        summary = fallback_summary
        task_anchor = ""
        plan = []
        if isinstance(raw, dict):
            hist = raw.get("history") if isinstance(raw.get("history"), list) else []
            summary = str(raw.get("summary") or summary or "")
            task_anchor = str(raw.get("task_anchor") or "")
            plan = raw.get("plan") if isinstance(raw.get("plan"), list) else []
        elif isinstance(raw, list):
            hist = raw
        else:
            return
        if hist or summary or plan:
            self.agent = self._build_main_agent()
            self.agent.history = hist
            if summary:
                self.agent._summary = summary
            if task_anchor:
                self.agent._task_anchor = task_anchor
            if plan:
                self.agent.plan = plan
                self._render_plan(plan)          # 恢复后计划面板也重新钉出来，别让它看着像丢了
            self._chrome("[dim]↻ 已恢复对话上下文（主 agent 记得之前的对话）[/dim]")
            self._render_statusbar()

    def _cmd_new(self) -> None:
        from src.llm.client import reset_usage
        self.session_id = self.sessions.start_session()
        self.agent = None                   # 新会话 = 全新 agent 上下文
        self._allow_writes_session = False  # "始终允许"也随新会话复位
        self._allow_commands_session = False
        reset_usage()                       # 用量也清零
        self._render_plan([])               # 收起上个会话的计划面板
        from src.agents.shell import stop_all_background
        n_bg = stop_all_background()        # 收掉上个会话遗留的后台命令，别泄漏 dev server 进程
        if n_bg:
            self._chrome(f"[dim]■ 已停止 {n_bg} 个后台命令[/dim]")
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
        msg = (f"本会话用量（估算）: 调用 {u['calls']} 次 · 输入 ~{u['prompt_tokens']} · "
               f"输出 ~{u['completion_tokens']} · 合计 ~{u['total_tokens']} tokens")
        cached = u.get("cached_tokens", 0)
        if cached:                              # 上游缓存确有命中 → 报命中率，证明缓存在自有中转生效
            pt = max(1, u.get("prompt_tokens", 0))
            msg += f"\n其中输入命中缓存 ~{cached} tokens（约 {int(cached * 100 / pt)}%，上游 prompt 缓存已生效）"
        else:
            msg += "\n[输入缓存未观测到命中：上游/中转未回报 cached_tokens，或本会话前缀尚未复用]"
        ctx = self._context_usage_label()
        if ctx:
            msg += f"\n当前上下文占用（估算）: {ctx}"
        self._emit(msg)

    def _set_context_policy(self, policy: str) -> str | None:
        from src.agents.main_agent import _normalize_context_policy
        normalized = _normalize_context_policy(policy)
        if normalized != policy.strip().lower():
            return None
        self._context_policy = normalized
        self._save_setting("context_policy", normalized)
        if self.agent is not None:
            self.agent.context_policy = normalized
        self._render_statusbar()
        return normalized

    def _cmd_context(self, arg: str) -> None:
        """/context：查看上下文占用；/context auto|compact|balanced|preserve 切策略并持久化。"""
        arg = (arg or "").strip().lower()
        if arg:
            policy = self._set_context_policy(arg)
            if policy is None:
                self._emit("用法: /context [auto|compact|balanced|preserve]")
                return
            self._chrome(f"[green]上下文策略已切换为 {policy}（已写入 .vortocode/settings.json）[/green]")
        if self.agent is None:
            self.agent = self._build_main_agent()
        try:
            u = self.agent.context_usage(self.mode)
        except Exception as e:  # noqa: BLE001
            self._emit(f"上下文统计失败: {e}")
            return

        def tok(name: str) -> str:
            return self._fmt_tokens_short(int(u.get(name, 0)))

        raw_policy = str(u.get("raw_policy") or "auto")
        effective = str(u.get("policy") or raw_policy)
        lines = [
            "[b]上下文窗口[/b]（估算，按当前下一轮请求计算）",
            f"策略: {raw_policy} → 当前生效 {effective}",
            f"占用: {tok('used_tokens')}/{tok('max_context_tokens')} tokens · {int(u.get('pct', 0))}%",
            "",
            "分解:",
            f"  system: {tok('system_tokens')}",
            f"  history(保留): {tok('history_tokens')} / 原始 {tok('raw_history_tokens')}",
            f"  summary: {tok('summary_tokens')}",
            f"  plan: {tok('plan_tokens')}",
            f"  messages: {int(u.get('trimmed_history_messages', 0))}/{int(u.get('history_messages', 0))}",
            "",
            "压缩:",
            f"  compact: {'on' if u.get('compact_enabled') else 'off'}",
            f"  当前策略触发线: {tok('max_context_tokens')}",
            f"  压缩后最近原文预算: {tok('recent_budget')}",
            f"  下一轮会压缩: {'yes' if u.get('will_compact') else 'no'}",
            "",
            "可选策略:",
            "  /context auto      plan=balanced, build=preserve",
            "  /context compact   日常轻量对话，更早压缩",
            "  /context balanced  普通协作",
            "  /context preserve  高强度开发，尽量保留原文",
        ]
        if effective == "preserve":
            lines.append("\n建议: 当前适合开发/调试长任务；如果只是问答，可切 /context compact 节省上下文。")
        elif effective == "compact":
            lines.append("\n建议: 当前适合日常对话；进入长任务或 PR 审核前可切 /context preserve。")
        else:
            lines.append("\n建议: auto 会随 plan/build 自动调整；大多数项目默认用它。")
        self._emit("\n".join(lines))

    def _compact_preview_text(self, data: dict) -> str:
        return (
            f"将压缩旧消息 {int(data.get('older_messages', 0))} 条"
            f"（~{self._fmt_tokens_short(int(data.get('older_tokens', 0)))} tok），"
            f"保留最近 {int(data.get('recent_messages', 0))} 条"
            f"（~{self._fmt_tokens_short(int(data.get('recent_tokens', 0)))} tok）。"
        )

    def _cmd_compact(self, arg: str) -> None:
        """/compact：手动压缩旧对话；/compact preview 只预估。"""
        arg = (arg or "").strip().lower()
        if arg not in ("", "preview"):
            self._emit("用法: /compact [preview]")
            return
        if self.agent is None:
            self.agent = self._build_main_agent()
        preview = self.agent.compact_preview(self.mode)
        if arg == "preview":
            status = "可压缩" if preview.get("can_compact") else "暂不可压缩"
            self._emit(f"上下文压缩预览：{status}\n{self._compact_preview_text(preview)}")
            return

        async def _run():
            result = await self.agent.compact_now(self.mode)
            if not result.get("ok"):
                self._emit(f"上下文压缩未执行：{result.get('reason', '未知原因')}\n"
                           f"{self._compact_preview_text(result)}")
                return
            self._audit_event("compact", {
                "before_messages": result.get("before_messages"),
                "after_messages": result.get("after_messages"),
                "before_tokens": result.get("before_tokens"),
                "after_tokens": result.get("after_tokens"),
                "summary_len": len(result.get("summary") or ""),
            })
            self._render_statusbar()
            self._emit("上下文已压缩。\n"
                       f"{self._compact_preview_text(result)}\n"
                       f"消息数: {result['before_messages']} → {result['after_messages']}；"
                       f"history tokens: ~{self._fmt_tokens_short(int(result['before_tokens']))}"
                       f" → ~{self._fmt_tokens_short(int(result['after_tokens']))}")
        self.run_worker(_run(), exclusive=True, group="compact")

    def _cmd_theme(self, arg: str) -> None:
        """/theme：无参弹主题选择器（↑↓ **实时预览**，回车定、Esc 恢复）；带参直接切。"""
        names = sorted(self.available_themes)
        name = arg.strip()
        if not name:
            cur = self.theme
            items = [(n, f"{n}{'  ← 当前' if n == cur else ''}") for n in names]

            def _preview(n) -> None:        # 高亮即套用（opencode 式实时预览）
                if n in self.available_themes:
                    self.theme = n

            def _done(sel) -> None:
                if sel and sel in self.available_themes:
                    self.theme = sel
                    self._persist_theme(sel)
                    self._chrome(f"[green]→ 主题切到 {sel}（已记住）[/green]")
                else:
                    self.theme = cur        # Esc：恢复进弹窗前的主题
            self.push_screen(ListPicker("选择主题 · ↑↓ 实时预览", items, on_highlight=_preview,
                                        initial=cur), _done)
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

    def _auto_recall(self, text: str):
        """回合前按用户话**自动召回**最相关的几条长期记忆，作为轻量上下文注入。

        默认开（env VORTOCODE_AUTO_RECALL=0 关）。严格封顶（top 3、每条 ≤200 字、总 ≤600 字）避免撑爆
        上下文预算；只召回**用户已确认存过**的记忆（非自动写入，风险低）；透明提示、非静默。
        返回 {n, text} 或 None。"""
        import os
        if os.getenv("VORTOCODE_AUTO_RECALL", "1").strip().lower() in ("0", "false", "no", "off"):
            return None
        q = (text or "").strip()
        if len(q) < 6:                       # 太短（寒暄/单字）没检索价值，免得乱注入
            return None
        try:
            rows = self.sessions.store.search_memories("__longterm__", q, 3)
        except Exception:  # noqa: BLE001
            return None
        parts, total = [], 0
        for r in rows or []:
            c = " ".join(str(r.get("content", "")).split())[:200]
            if not c or total + len(c) > 600:
                continue
            parts.append(f"- {c}")
            total += len(c)
        return {"n": len(parts), "text": "\n".join(parts)} if parts else None

    # ---------------------------------------------------------------- 主 agent loop
    @work(exclusive=True, group="action")
    async def _route(self, text: str) -> None:
        """普通话（非 / 命令）入口：交给主 agent loop。

        主 agent 自己决定是聊天/读代码/扫描（只读工具），还是把正经开发任务交给隔离 dev 工具
        （dev_isolated/dev_parallel/dev_auto，重型、build 模式）—— 不再需要前置意图分类器，闲聊天然由它处理。
        attach 模式（--attach）：回合交常驻 serve 跑，本 TUI 只是协议客户端（渲染 + confirm 应答）；
        serve 够不着（连接阶段失败）→ 提示一行、本回合回退进程内。
        """
        import os
        user_text, ctx = self._expand_context(text)
        images = getattr(self, "_turn_images", []) or []      # @图片 → 多模态附件
        audio = getattr(self, "_turn_audio", []) or []        # @音频 → 多模态附件
        if ctx:
            self._chrome(f"[dim]＋ 已注入 @提及的上下文（{len(ctx)} 字）[/dim]")
            user_text = f"{user_text}\n\n[@提及的上下文]\n{ctx}"
        recalled = self._auto_recall(text)                    # 自动召回相关长期记忆（默认开、封顶、透明）
        if recalled:
            self._chrome(f"[dim]＋ 召回 {recalled['n']} 条相关长期记忆[/dim]")
            user_text = f"{user_text}\n\n[相关长期记忆（自动召回，供参考；不一定切题）]\n{recalled['text']}"
        if images:
            self._chrome(f"[dim]🖼 附带 {len(images)} 张图（mimo-v2.5 可读图）[/dim]")
        if audio:
            self._chrome(f"[dim]🎧 附带 {len(audio)} 段音频（mimo-v2.5 可听音频）[/dim]")
        user_text = f"{user_text}\n\n{self._mode_context()}"
        if self._attach_url:
            if await self._route_attached(user_text, images, audio):
                return                       # attach 回合完成（含 serve 侧出错——已如实渲染，不重跑）
            self._chrome(f"[yellow]⚠ 未连上 serve（{self._attach_url}），本回合进程内执行[/yellow]")
        if not os.getenv("OPENAI_API_KEY"):
            self._chrome("[green]对话[/green] [dim](未配置 OPENAI_API_KEY)[/dim]")
            self._emit("配置 OPENAI_API_KEY 后即可自由对话/开发（见 .env）。")
            self._emit("现在无需 key 也能用：/analyze 扫描本仓库、/help 看全部命令。")
            return
        if self.agent is None:
            self.agent = self._build_main_agent()
        think_cb, stream_cb, emit_final, cleanup = self._turn_renderers()
        self._turn_tools = 0
        t0 = time.monotonic()
        reply = ""
        try:
            reply = await self.agent.run_turn(user_text, mode=self.mode, say=self._turn_say,
                                              emit=emit_final, stream_cb=stream_cb,
                                              images=images, audio=audio, reasoning_cb=think_cb)
        finally:
            cleanup()                        # 出错/取消也收干净
            self._fold_tool_activity()       # 工具活动折叠成一行摘要（错误/取消也不丢轨迹）
        if self._turn_tools:                # 用过工具的回合给个清晰收尾
            self._chrome(f"[dim]✓ 完成 · {time.monotonic() - t0:.0f}s[/dim]")
        if self._speak_replies and reply:   # /speak 开：把这条回复合成语音朗读
            self._chrome("[dim]🔊 合成语音中…[/dim]")
            await self._speak_text(reply)
        self._persist_agent_history()
        self._render_statusbar()

    def _turn_renderers(self):
        """一个回合的四件套 UI 渲染闭包（进程内与 attach 两条路径共用，保证同一观感）：

        (think_cb 思维链摘要→log, stream_cb 流式摘要→log, emit_final 最终回复→log, cleanup 收尾)。
        RichLog 是追加型，流式正文以节流快照进入同一结果流；最终回复仍按 markdown 落地。
        """
        self._turn_tool_lines.clear()           # 新回合从零开始收工具活动（防上回合残留）
        self._turn_tool_counts.clear()
        self._turn_tool_previewed = 0
        think_buf = []                          # 累积推理型模型的思维链（reasoning_content）
        think_state = {"started": False, "last": 0.0, "updates": 0}
        stream_cb = self._make_stream_preview(label="vorto")

        def _write_thinking_line(text: str) -> None:
            self.transcript.append(text)
            self.query_one("#log", RichLog).write(Text(text, style="dim"))

        def think_cb(delta: str) -> None:
            # 思考呈现：放进主结果区，而不是输入框上方的临时小框。节流追加，避免 reasoning token
            # 把正文刷走；没有 reasoning_content 的模型自然不触发、零打扰。
            if not self._show_thinking:
                return
            think_buf.append(delta)
            now = time.monotonic()
            tail = " ".join("".join(think_buf)[-900:].splitlines()[-3:]).strip()
            if not think_state["started"]:
                think_state["started"] = True
                think_state["last"] = now
                _write_thinking_line(f"💭 思考中… {tail[:180]}" if tail else "💭 思考中…")
                return
            if think_state["updates"] >= 3 or now - think_state["last"] < 0.8:
                return
            if tail:
                _write_thinking_line(f"  {tail[:240]}")
                think_state["updates"] += 1
                think_state["last"] = now

        def emit_final(text: str) -> None:
            self._assistant(text)

        def cleanup() -> None:
            return None

        return think_cb, stream_cb, emit_final, cleanup

    async def _route_attached(self, user_text: str, images: list, audio: list) -> bool:
        """attach 模式跑一个回合：serve 是唯一状态所有者，TUI 只渲染 + confirm 应答。

        返回 True=回合已完成（含 serve 侧出错——已如实渲染，**不回退重跑**，防重复执行）；
        False=连接阶段失败（回合未发出，调用方安全回退进程内）。
        协议事件 → UI 面映射：say→_chrome、stream→结果区摘要、reasoning→结果区摘要、
        plan→计划面板、confirm→ConfirmScreen 应答回传、emit/done→收尾。
        富 UI 取舍（v1，如实交代）：工具在 serve 端跑（与 Web 同一工厂），TUI 的着色 diff
        直写工具在 attach 下不参与；进程内模式保留全部富 UI。
        """
        from src.gateway import protocol as gp
        from src.gateway.client import ProtocolClient, local_ref_to_data_url
        from src.web.auth import get_api_token

        client = ProtocolClient(self._attach_url, sid=f"tui-{self.session_id or 'default'}",
                                token=get_api_token() or None)
        try:
            await client.__aenter__()
        except Exception:  # noqa: BLE001 —— 回合未发出，调用方回退进程内
            return False
        think_cb, stream_cb, emit_final, cleanup = self._turn_renderers()

        async def confirm(message: str) -> bool:
            # serve 端工具的确认经协议回到 TUI 内联选择：**始终问**、不吃本地"始终允许"豁免
            # （scope 信息不过协议，宁多问不越权；与 _confirm_outward 同一保守面）。
            return bool(await self._inline_confirm(message, scope="writes"))

        t0 = time.monotonic()
        try:
            if client.server_version not in (None, gp.PROTOCOL_VERSION):
                self._chrome(f"[yellow]⚠ serve 协议版本 v{client.server_version} ≠ "
                             f"本端 v{gp.PROTOCOL_VERSION}，事件面可能不齐[/yellow]")
            outcome = await client.run_turn(
                user_text, mode=self.mode,
                images=[local_ref_to_data_url(i) for i in images],
                audio=[local_ref_to_data_url(a, is_audio=True) for a in audio],
                on_say=self._turn_say, on_stream=stream_cb, on_emit=emit_final,
                on_plan=self._render_plan, on_reasoning=think_cb, confirm=confirm)
        finally:
            cleanup()
            self._fold_tool_activity()       # serve 侧回合同样折叠（同一观感）
            await client.__aexit__()
        if outcome.status == "error":
            self._emit(f"[red]serve 回合出错：{outcome.error}[/red]")
        elif outcome.status == "cancelled":
            self._chrome("[yellow]serve 侧已中断[/yellow]")
        else:
            self._chrome(f"[dim]✓ 完成（serve）· {time.monotonic() - t0:.0f}s[/dim]")
        if self._speak_replies and outcome.reply:      # /speak 开：本地合成朗读（与进程内同路径）
            self._chrome("[dim]🔊 合成语音中…[/dim]")
            await self._speak_text(outcome.reply)
        return True

    def _agent_snapshot(self) -> str:
        """主 agent 可恢复状态；兼容历史压缩后继续对话。"""
        data = {
            "version": 2,
            "history": getattr(self.agent, "history", [])[-40:],
            "summary": getattr(self.agent, "_summary", ""),
            "task_anchor": getattr(self.agent, "_task_anchor", ""),
            "plan": getattr(self.agent, "plan", []),
        }
        return json.dumps(data, ensure_ascii=False)

    def _persist_agent_history(self) -> None:
        """把 agent 当前上下文快照进会话，供 /resume 跨会话续上记忆。失败不影响交互。"""
        if not (self.agent and self.session_id and self._persist_on):
            return
        try:
            self.sessions.add_message("agent", self._agent_snapshot(), {"agent_history": True})
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
        （edit_file/dev_isolated/dev_auto/run_command…）仅 build —— 这就是 opencode Plan/Build 的"工具权限门"。
        """
        from src.agents.main_agent import MainAgent, Tool

        def _safe_path(rel: str):
            """把相对路径锁在仓库内，防止 ../ 或绝对路径越界。返回 Path 或 None。"""
            base = Path(self.repo_root).resolve()
            try:
                p = (base / rel).resolve()
            except Exception:  # noqa: BLE001
                return None
            return p if (p == base or base in p.parents) else None

        async def _t_edit_file(args: dict) -> str:
            from src.agents.main_agent import _truthy
            rel = str(args.get("path", "")).strip().lstrip("@")
            old, new = str(args.get("old", "")), str(args.get("new", ""))
            all_ = _truthy(args.get("replace_all", args.get("all")))
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
            if cnt > 1 and not all_:
                return (f"原文在 {rel} 中出现 {cnt} 次、不唯一；请给更长、唯一的 old，"
                        f"或传 replace_all=true 一次替换全部 {cnt} 处。")
            n = cnt if all_ else 1
            ok = await self._confirm_write(
                f"build 模式：修改 {rel}？替换 {n} 处（{len(old)}→{len(new)} 字符）。改动只进工作区，不碰 main。")
            if not ok:
                return f"用户取消了对 {rel} 的修改。"
            p.write_text(text.replace(old, new, n), encoding="utf-8")
            self._show_diff(rel, old, new)        # 着色 diff 进对话区（仿 Claude Code）
            self._chrome(f"[green]已修改 {rel}（{n} 处；请 review；/diff 或 git diff 看全）[/green]")
            return f"已修改 {rel}（替换 {n} 处）。"

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

        async def _t_rename_symbol(args: dict) -> str:
            """语义重命名（jedi）：项目级把 symbol 改成 new_name，预览 diff → 确认 → 落工作区。"""
            from src.agents.lsp import compute_rename
            symbol = str(args.get("symbol", "")).strip()
            new_name = str(args.get("new_name") or args.get("new") or "").strip()
            if not symbol or not new_name:
                return "rename_symbol 需要 symbol 和 new_name。"
            r = compute_rename(self.repo_root, symbol, new_name)
            if not r.get("ok"):
                return r.get("error", "重命名失败。")
            files = r["files"]
            self._chrome(f"[magenta]✎ 语义重命名 {symbol} → {new_name}[/magenta]"
                         f"[dim]（{r['count']} 个文件，定义于 {r.get('definition', '?')}）[/dim]")
            self._render_diff_text(r["diff"], max_lines=400)   # 预览多文件 diff
            ok = await self._confirm_write(
                f"build 模式：把 `{symbol}` 语义重命名为 `{new_name}`？将改 {r['count']} 个文件"
                f"（{', '.join(list(files)[:6])}{'…' if len(files) > 6 else ''}）。改动只进工作区，不碰 main。")
            if not ok:
                return f"用户取消了重命名 {symbol} → {new_name}。"
            written = []
            for rel, content in files.items():
                p = _safe_path(rel)
                if p is None or not p.is_file():
                    continue                               # 越界/不存在 → 跳过（compute 已挡越界，双保险）
                p.write_text(content, encoding="utf-8")
                written.append(rel)
            self._chrome(f"[green]已重命名 {symbol} → {new_name}，改了 {len(written)} 个文件"
                         f"（请 review；/diff 看全）[/green]")
            return f"已把 {symbol} 语义重命名为 {new_name}，修改 {len(written)} 个文件：{', '.join(written)}"

        async def _t_dev_isolated(args: dict) -> str:
            """隔离 worktree 里实现一步 + 在其中跑测试逐件验证，产出 diff（绿=可应用）待人工确认。"""
            desc = str(args.get("description") or args.get("task") or args.get("goal") or "").strip()
            if not desc:
                return "dev_isolated 需要 description（要在隔离工作区实现的任务）。"
            import uuid
            from src.agents.worktree import run_isolated_task
            from src.agents.main_agent import build_read_tools, build_write_tools
            from src.agents.test_detect import detect_test_cmd
            wid = "wt-" + uuid.uuid4().hex[:8]
            sel = str(args.get("test") or "").strip()                 # 可选：narrow 到某些测试
            test_cmd = detect_test_cmd(self.repo_root, sel)           # 按仓库类型探测（pytest/npm/go/cargo/make）
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
            import uuid
            from src.agents.worktree import run_isolated_task, apply_diffs_to_branch
            from src.agents.main_agent import build_read_tools, build_write_tools
            from src.agents.test_detect import detect_test_cmd
            sel = str(args.get("test") or "").strip()
            test_cmd = detect_test_cmd(self.repo_root, sel)           # 按仓库类型探测（pytest/npm/go/cargo/make）
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
                # 传 test_cmd：落分支后在集成分支上再跑一遍全量，抓"单独绿合起来红"的语义冲突——
                # 各块只在自己的隔离 worktree 单独绿过，合到一起可能相互破坏。此前 TUI 没传 test_cmd，
                # 集成测试根本不跑（apply_diffs_to_branch 的 integration 恒为 None），会静默漏掉语义冲突。
                res = await asyncio.to_thread(apply_diffs_to_branch, self.repo_root, branch, items, test_cmd)
                integ = res.get("integration")
                if res["applied"] and integ and not integ["ok"]:      # 单独绿、合起来红 → 如实说，别谎报
                    self._chrome(f"[{self._tc('text-warning', '#f0b86e')}]⚠️ {len(res['applied'])} 块已落到 "
                                 f"[b]{branch}[/b]，但**集成后全量测试未过**（单独绿、合起来红，多为语义冲突/"
                                 f"相互破坏），分支保留待修[/]")
                    note = (f"，{len(res['applied'])}/{len(greens)} 块落到 {branch} 但**集成红**（单独绿合起来红）；"
                            f"失败尾部：\n{integ['output'][-800:]}\n分支已留：git checkout {branch} 据此修正")
                elif res["applied"]:
                    ok_note = "且集成测试通过" if integ else ""      # test_cmd 恒有，integ 正常不为 None
                    self._chrome(f"[{self._tc('text-success', '#7fce9a')}]✅ 已应用 {len(res['applied'])} 块到分支 "
                                 f"[b]{branch}[/b]{ok_note}（git checkout {branch} 查看）[/]")
                    note = f"，{len(res['applied'])}/{len(greens)} 块已应用到 {branch}{ok_note}"
                if res["failed"]:
                    self._chrome(f"[{self._tc('text-warning', '#f0b86e')}]{len(res['failed'])} 块未能干净应用"
                                 f"（可能互相冲突），已跳过[/]")
                    # 也写进返回值：否则主 agent 只看到"N 块已应用"、以为都进去了，被丢的块被静默漏报
                    note += (f"；⚠️ {len(res['failed'])} 块虽自测绿但**文本冲突、未能干净落分支**（已跳过）："
                             + "、".join(d.get("msg", "?") for d in res["failed"]))
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
            """跑任意 shell 命令（测试/lint/git/构建…）。高危：build 门控 + 人工确认 + 危险拦截。
            background=true 则后台起长驻进程（dev server/watcher/tail），立即返回句柄、不阻塞回合。"""
            cmd = str(args.get("command") or args.get("cmd") or "").strip()
            if not cmd:
                return "run_command 需要 command。"
            from src.agents.main_agent import _truthy
            bg = _truthy(args.get("background"))
            from src.agents.shell import is_dangerous, run_command, run_command_background
            why = is_dangerous(cmd)
            if why:                                   # 兜底硬拒（即便始终允许）
                self._chrome(f"[{self._tc('text-error', '#f08a8a')}]拒绝执行（{why}）：{cmd}[/]")
                return f"拒绝执行（疑似危险操作：{why}）。请换更具体、安全的命令。"
            label = "后台启动" if bg else "执行命令"
            if not await self._confirm_command(
                    f"build 模式：在仓库根目录{label}？\n  $ {cmd}\n（可能改动工作区，但不碰 main）"):
                return f"用户取消了命令：{cmd}"
            import asyncio
            if bg:
                self._chrome(f"[dim]$ {cmd}  [/dim][{self._tc('text-warning', '#f0b86e')}]&（后台）[/]")
                res = await asyncio.to_thread(run_command_background, self.repo_root, cmd)
                if not res.get("ok"):
                    self._chrome(f"[{self._tc('text-error', '#f08a8a')}]后台启动失败：{res.get('error')}[/]")
                    return f"后台启动失败：{res.get('error')}"
                self._chrome(f"[{self._tc('text-success', '#7fce9a')}]▸ 已后台启动 {res['id']}（pid {res['pid']}）[/]")
                return (f"已后台启动命令 `{cmd}`，句柄 {res['id']}（pid {res['pid']}）。"
                        f"用 read_output(id={res['id']}) 看输出、stop_command(id={res['id']}) 停止。")
            self._chrome(f"[dim]$ {cmd}[/dim]")
            res = await asyncio.to_thread(run_command, self.repo_root, cmd)
            out = res["output"]
            if out.strip():
                self._chrome(f"[dim]{out[-1500:].replace('[', chr(92) + '[')}[/dim]")
            ok_c = self._tc("text-success", "#7fce9a") if res["ok"] else self._tc("text-error", "#f08a8a")
            self._chrome(f"[{ok_c}]{'✓' if res['ok'] else '✗'} exit {res['code']}[/]")
            return f"命令 `{cmd}` 退出码 {res['code']}。输出尾部：\n{out[-3000:]}"

        async def _t_read_output(args: dict) -> str:
            """读某后台命令的新增输出（或 tail=N 看最近 N 行）+ 运行状态。只读，无需确认。"""
            from src.agents.shell import read_background
            bid = str(args.get("id") or args.get("bid") or "").strip()
            if not bid:
                return "read_output 需要 id（后台命令句柄，如 bg1）。"
            tail = args.get("tail")
            tail = int(tail) if str(tail).strip().isdigit() else None
            res = await asyncio.to_thread(read_background, bid, tail)
            if not res.get("ok"):
                return res.get("error", "读取失败")
            head = f"[{res['id']}] {res['status']}" + (f"（退出码 {res['code']}）" if res['code'] is not None else "")
            drop = f"\n（⚠ 有 {res['dropped']} 行因缓冲上限被挤掉、未读到）" if res.get("dropped") else ""
            body = res["output"] or "(暂无新输出)"
            return f"{head}{drop}\n{body[-3000:]}"

        async def _t_stop_command(args: dict) -> str:
            """停某后台命令（terminate→kill）。低风险、无需确认。"""
            from src.agents.shell import stop_background
            bid = str(args.get("id") or args.get("bid") or "").strip()
            if not bid:
                return "stop_command 需要 id。"
            res = await asyncio.to_thread(stop_background, bid)
            if not res.get("ok"):
                return res.get("error", "停止失败")
            self._chrome(f"[dim]■ 已停止后台命令 {bid}[/dim]")
            return f"已停止后台命令 {bid}（退出码 {res.get('code')}）。"

        # 只读工具：plan 也能用；也是子 agent 的工具集（无 task/写工具 → 不嵌套、不改文件）。
        # 直接复用 build_read_tools——TUI 至此与 web/CLI 同源，白拿 read_file 行段 / 全仓库 grep /
        # find_definition·find_references·document_symbols（jedi 语义导航）/ git_status·show_diff·list_branches。
        from src.agents.main_agent import build_read_tools, build_web_tools
        read_tools = build_read_tools(self.repo_root) + build_web_tools()   # +web_fetch（查文档/issue/报错页）

        async def _spawn_research(desc: str, agent_name: str = "") -> str:
            """起一个隔离子 agent，返回结论。task 与 research_parallel 共用。

            agent_name 非空 → 按 .vortocode/agents/<名>.md 装配自定义角色（与工厂版同一注册表/
            同一安全面）；dev 型角色过 _confirm_write 人闸（headless 之外 TUI 有真人在）。"""
            if agent_name:
                from src.agents.main_agent import build_subagent
                from src.agents.subagents import registry_for
                reg = registry_for(self.repo_root)
                spec = reg.get(agent_name)
                if spec is None:
                    avail = "、".join(reg.specs) or "（无——在 .vortocode/agents/ 放 <名>.md 定义角色）"
                    return f"没有名为 {agent_name!r} 的子 agent。可用：{avail}"
                sub = build_subagent(self.repo_root, spec, confirm=self._confirm_write,
                                     on_progress=lambda m: self._chrome(f"[dim]{m}[/dim]"))
                if spec.tools == "dev" and not await self._confirm_write(
                        f"委派角色「{agent_name}」用隔离 dev 流水线实现：{desc[:120]}\n"
                        f"（产出落 vorto/* 分支，不碰主工作区）"):
                    return f"已取消：未放行 dev 型角色 {agent_name} 的委派。"
                sub._on_tool = self._audit_tool
                mode = "build" if spec.tools == "dev" else "plan"
            else:
                child_steps = 4 if self.mode == "plan" else 12
                sub = MainAgent(read_tools, max_steps=child_steps, on_tool=self._audit_tool, extra_system=(
                    "你是只读研究子 agent：只用工具调研代码/仓库并返回简洁结论，绝不修改任何东西。"
                    "读够信息就尽快收口，别把预算耗在重复读取上。"))
                mode = self.mode
            try:
                r = await sub.run_turn(desc, mode=mode, say=self._chrome, emit=lambda _t: None)
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
            agent_name = str(args.get("agent") or "").strip()
            self._chrome(f"[magenta]🤖 子 agent{f'「{agent_name}」' if agent_name else ''} 处理：{desc}[/magenta]")
            result = await _spawn_research(desc, agent_name)
            self._chrome(f"[dim]  ↳ 结论：{_preview(result)}[/dim]")   # 子 agent 结论可见
            return result

        async def _t_research_parallel(args: dict) -> str:
            tasks = args.get("tasks") or args.get("descriptions") or []
            if isinstance(tasks, str):
                tasks = [tasks]
            if self.mode == "plan":
                from src.agents.main_agent import research_parallel_cap
                max_tasks = research_parallel_cap(args, default=2, maximum=5)
            else:
                max_tasks = 5
            tasks = [str(t).strip() for t in tasks if str(t).strip()][:max_tasks]
            if not tasks:
                return "research_parallel 需要 tasks（字符串列表，每项一个独立子问题）。"
            import asyncio
            agent_name = str(args.get("agent") or "").strip()
            self._chrome(f"[magenta]🤖 并行子 agent（{len(tasks)}）研究中…[/magenta]")
            results = await asyncio.gather(*[_spawn_research(t, agent_name) for t in tasks])
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
            from src.agents.taint import is_tainted
            if is_tainted():        # 记忆是持久化注入面（ClawHavoc 教训）：标注来源含外部内容、可追溯
                import datetime
                content = f"[⚠ 来源含外部内容 · {datetime.date.today().isoformat()}] {content}"
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
                 {"description": "要委派的子任务",
                  "agent": "可选：自定义角色名（.vortocode/agents/ 里定义；缺省=只读研究员）"},
                 _t_task, read_only=True),
            Tool("save_memory", "把一条要跨会话长期记住的事实/偏好/约定存起来",
                 {"content": "要记住的内容"}, _t_save_memory, read_only=True),
            Tool("recall_memory", "检索跨会话长期记忆（不传 query 则列出全部）",
                 {"query": "可选，关键词"}, _t_recall_memory, read_only=True),
            Tool("research_parallel", "并行委派多个只读子 agent 同时研究不同子问题，汇总各自结论。"
                 "plan 默认最多 2 个；用户明确要求全面/多角度/深挖时，可传 max_parallel 和 reason 放宽到 5。",
                 {"tasks": "子问题字符串列表",
                  "max_parallel": "可选，并行子 agent 数；plan 默认 2，需配合 reason 才能超过默认，硬上限 5",
                  "reason": "可选；说明为什么需要超过默认并行度，如用户明确要求全面审查/多角度分析",
                  "agent": "可选：自定义角色名（应用到本组全部子任务）"},
                 _t_research_parallel, read_only=True),
            Tool("use_skill", "加载某个技能(SKILL.md)的完整指令到上下文，然后据此执行",
                 {"name": "技能名"}, _t_use_skill, read_only=True),
            Tool("save_skill", "把一套可复用流程保存成新技能(SKILL.md)到用户技能目录；写操作，需确认，仅 build",
                 {"name": "技能名", "description": "一句话描述", "instructions": "技能正文（自然语言步骤）"},
                 _t_save_skill, read_only=False),
            Tool("edit_file", "对仓库文件做精确字符串替换：默认 old 须唯一（替 1 处）；old 出现多次时传 "
                 "replace_all=true 一次替换全部。写操作，需确认，仅 build",
                 {"path": "相对路径", "old": "要替换的原文", "new": "替换为",
                  "replace_all": "可选，true=替换全部出现处"},
                 _t_edit_file, read_only=False),
            Tool("write_file", "新建或覆盖仓库文件；写操作，需确认，仅 build",
                 {"path": "相对路径", "content": "文件全部内容"}, _t_write_file, read_only=False),
            Tool("rename_symbol",
                 "语义重命名（jedi/LSP 级，跟随 import、改所有引用，比 find+replace 安全）：把某函数/"
                 "类/变量改名，预览多文件 diff→确认→落工作区；写操作，仅 build",
                 {"symbol": "现有符号名", "new_name": "新名（合法标识符）"},
                 _t_rename_symbol, read_only=False),
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
                 _t_open_pr, read_only=False, outward=True),
            Tool("run_command",
                 "在仓库根目录跑任意 shell 命令（如 pytest 某个文件 / ruff / git log / pip install / make）；"
                 "高危，每条都需确认、明显危险操作直接拒（仅 build）。长驻命令（dev server / npm run dev / "
                 "watch / tail -f）传 background=true 后台起、立即返回句柄，再用 read_output 看输出",
                 {"command": "要执行的 shell 命令",
                  "background": "可选，true=后台起长驻进程（不阻塞回合），用 read_output/stop_command 管理"},
                 _t_run_command, read_only=False, outward=True),
            Tool("read_output",
                 "读某后台命令（run_command background=true 起的）的**新增**输出 + 运行状态；"
                 "传 tail=N 看最近 N 行。只读、无需确认",
                 {"id": "后台命令句柄，如 bg1", "tail": "可选，看最近 N 行（默认给上次读之后的增量）"},
                 _t_read_output, read_only=True),
            Tool("stop_command",
                 "停掉某后台命令（terminate→kill）。用完 dev server / watcher 记得收摊",
                 {"id": "后台命令句柄，如 bg1"}, _t_stop_command, read_only=True),
        ]

        # dev_auto（一句话→自动分解→并行/接力实现→集成→可选开 PR）：复用**工厂版**（自主流水线，
        # 靠隔离 + build 门 + 落 vorto/auto 分支保关口），进度走 _chrome、开 PR 外向确认走 _confirm_outward。
        # 只取 dev_auto——工厂还返回朴素 dev_isolated/dev_parallel，一并加会覆盖上面 TUI 的富 UI 版。
        from src.agents.main_agent import build_dev_tools as _factory_dev_tools
        tools += [t for t in _factory_dev_tools(self.repo_root, on_progress=self._chrome,
                                                confirm=self._confirm_outward)
                  if t.name in ("dev_auto", "dev_resume", "pr_fix")]

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
        from src.agents.project import load_project_instructions
        catalog = registry.catalog()
        extra_parts = []
        proj = load_project_instructions(self.repo_root)     # AGENTS.md/CLAUDE.md 项目约定进系统提示
        if proj:
            extra_parts.append(proj)
        if catalog:
            extra_parts.append(f"【可用技能】(需要时用 use_skill 加载其完整指令再执行)\n{catalog}")
        from src.agents.subagents import subagent_catalog
        agents_cat = subagent_catalog(self.repo_root)        # 自定义角色目录（task 的 agent 参数按名委派）
        if agents_cat:
            extra_parts.append("【可用子 agent】(用 task/research_parallel 的 agent 参数按名委派；"
                               "dev 型角色经隔离流水线写代码、需确认)\n" + agents_cat)
        extra = "\n\n".join(extra_parts) if extra_parts else None
        from src.agents.main_agent import native_default
        native = native_default()                # 三端统一 native 开关（收敛到 main_agent.native_default）
        hook_system = self._load_hook_system()   # .vortocode/hooks.yaml 存在才接，避免无谓开销
        from src.agents.permissions import load_permissions
        agent = MainAgent(tools, max_steps=16, extra_system=extra, native=native,
                          on_tool=self._audit_tool, on_escalate=self._escalate_to_build,
                          on_plan=self._render_plan, plan_tool=True, hook_system=hook_system,
                          context_policy=self._context_policy,
                          permissions=load_permissions(self.repo_root),   # .vortocode/permissions.yaml deny
                          env_context=True)                # 顶层交互 agent：注入 <env>（cwd/git/日期/目录）
        if self._model_override:            # /model 切过 → 新建的 agent 也带上（重建时不丢）
            try:
                agent.set_model(self._model_override)
            except Exception:  # noqa: BLE001
                pass
        return agent

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
        """把主 agent 的任务清单渲染成**常驻**进度面板（钉在输入框上方，每次 update_plan 原地重渲、
        不随对话日志滚走）。空计划则收起面板，不占地方。"""
        from src.agents.plan import plan_progress
        try:
            panel = self.query_one("#plan", Static)
        except Exception:  # noqa: BLE001 —— 无头/尚未挂载时静默跳过
            return
        if not plan:                                    # 计划清空（/new、update_plan 传空）→ 收起
            panel.display = False
            panel.update("")
            self._plan_last = ""
            return
        done, total = plan_progress(plan)
        styles = {"completed": ("✓", self._tc("text-success", "#7fce9a")),
                  "in_progress": ("▸", self._tc("text-warning", "#f0b86e")),
                  "pending": ("○", "dim")}
        bar = "█" * done + "░" * max(0, total - done)   # 一眼可见的进度条
        lines = [f"[b]📋 计划[/b] [dim]{bar}[/] {done}/{total}"]
        for p in plan:
            status = p.get("status")
            glyph, color = styles.get(status, ("○", "dim"))
            step = str(p.get("step", "")).replace("[", r"\[")   # 防步骤文本里的方括号被当成标记
            # 进行中的一步加粗高亮，让"现在做到哪"一眼可辨
            step = f"[b]{step}[/b]" if status == "in_progress" else step
            lines.append(f"  [{color}]{glyph}[/] {step}")
        body = "\n".join(lines)
        panel.update(body)
        panel.display = True
        self._plan_last = body

    async def _escalate_to_build(self, name: str, args: dict) -> bool:
        """plan 模式下主 agent 想用写/重型工具时：问用户切不切 build，同意则切并继续。

        本会话已"始终允许"则直接切（不再问）。切了之后整个会话留在 build。
        """
        if self._allow_writes_session:
            ok = True
        else:
            if name == "request_build":
                reason = str(args.get("reason") or "").strip()
                next_action = str(args.get("next_action") or "").strip()
                parts = ["plan 阶段已完成必要分析，主 agent 请求切到 build 模式继续。"]
                if reason:
                    parts.append(f"原因：{reason}")
                if next_action:
                    parts.append(f"下一步：{next_action}")
                parts.append("切到 build 模式并继续？")
                prompt = "\n".join(parts)
            else:
                prompt = f"plan(只读)模式下，这一步要用写/重型工具「{name}」。切到 build 模式并继续？"
            ok = await self._inline_confirm(prompt, scope="writes")
        if ok and self.mode != "build":
            self.mode = "build"
            self._sync_subtitle()
            self._chrome("[green]→ 已切到 build 模式并继续[/green]")
            self._record_mode_change()
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

    def _audit_event(self, event: str, data: dict) -> None:
        """把非工具型事件写进审计日志，复用同一个 JSONL 入口。"""
        from datetime import datetime
        try:
            p = Path(self.repo_root) / ".vortocode" / "audit.log"
            p.parent.mkdir(parents=True, exist_ok=True)
            rec = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "mode": self.mode,
                "event": event,
                "data": data or {},
            }
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
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

        buf: list[str] = []
        preview = self._make_stream_preview(label="developer")

        def on_token(tok: str) -> None:
            buf.append(tok)
            preview("".join(buf))

        async def on_iter(record) -> None:
            ok = "✓" if record.tests_passed else "✗"
            self._emit(f"  第{record.iteration}轮 · 测试{ok} · 审查={record.review_verdict or '—'}")
            buf.clear()

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
        return summary


def run(attach: str | None = None) -> None:
    """启动 TUI（供 CLI 调用）。attach 非 None = 协议客户端模式（回合交常驻 serve 跑）。"""
    VortoCodeTUI(repo_root=".", attach=attach).run(mouse=False)
