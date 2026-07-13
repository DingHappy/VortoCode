"""确认门内核——「要不要问人、能不能免确认」的**唯一判定处**。

**为什么单独成模块**（2026-07-13）：这套判定是整个安全模型的心脏，却长期埋在 3.5k 行的
main_agent.py 里。后果有二：① 它进不了 mypy 的类型门禁圈（pyproject `[tool.mypy] files` 圈住了
write_policy / permissions / capabilities，唯独圈不进 gate 自己）；② gateway 只能惰性导入
main_agent 来躲导入重量。抽出来之后，安全判定是一个可被类型钉死、无重依赖的叶子模块。

**判定只有一处：`decide()`。** 三个前端（CLI / Web / IM）+ 跨进程 attach + 富 UI 的 TUI 全部消费它，
谁都不许在自己那头重写一遍这个排序——那正是"加一端漏一端"的历史病根（污点检查一度只写在 TUI 里，
CLI 的 `--yes` 和 Web 的确认门完全不查；attach 又自己手搓了一份）。端只负责**如实申报输入**
（问得到人吗？这次预授权了吗？本回合污点吗？）和**执行输出**（怎么问人、怎么如实交代）。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

# 判定三态。
ALLOW = "allow"          # 免确认放行（但调用方**必须留痕**，见 on_decision）
ASK = "ask"              # 必须问真人
DENY = "deny"            # 拒绝：问不到人，且没有（或已失效的）授权

TAINT_WARNING = ("⚠ 本回合已摄入外部内容（网页/搜索/MCP）。下面这个操作是模型在读过外部内容之后"
                 "提出的——请人工核对是否确是你的本意（防提示注入）：\n")

# 污点否决掉自动放行时给用户的**统一说法**。此前 CLI 的 attach 与 headless 各写了一份几乎相同的
# 文案，改一处漏一处——正是这套收敛要根除的抄贴漂移。
# （attach 另有一种"对端根本报不了污点状态"的情形，那是另一回事、另有文案，别混用。）
TAINT_REFUSED_REASON = "本回合摄入过外部内容，--yes 不放行（防提示注入）"


def taint_prefix() -> str:
    """污点态下给确认文案加的警示前缀（D0 防提示注入）；未污点返回空串。

    注：前缀只是"让人看见"。**真正的拦截是 `decide()`**——只加前缀拦不住自动放行
    （`--yes` 压根不看 message 内容长什么样）。
    """
    from src.agents.taint import is_tainted
    return TAINT_WARNING if is_tainted() else ""


def decide(*, tainted: bool, pre_authorized: bool, can_ask_human: bool) -> str:
    """**整个安全模型的唯一判定**：这次操作该放行、该问人，还是该拒？

    三个输入全部由端如实申报：

    - ``tainted``：本回合是否摄入过不可信外部内容（网页 / 搜索 / MCP）。进程内直接读 contextvar；
      跨进程 attach 由协议的结构化字段下发（读不到就按"可能有污点"兜底，绝不能靠猜文案）。
    - ``pre_authorized``：这一次是否已被授权免确认。**所有免确认授权都归到这一维**——CLI 的
      ``--yes``、TUI 的项目 allow 规则、TUI 的"始终允许"作用域，本质是同一件事。
      （TUI 的 ``force_prompt``——sandbox 降级执行——就是把这一维强制按回 False。）
    - ``can_ask_human``：这个端**问得到活人**吗（headless 非 TTY、无人值守 cron/heartbeat = 问不到）。

    规矩本身只有一句：

        **污点回合 → 一切预授权失效。** 能问到人就强制真人拍板；问不到人就拒。

    因为污点态下的"自动放行"正是提示注入最想要的落点：外部内容诱导出一个操作，再借用户早先给的
    免确认授权静默落地。那份授权在这一回合必须作废——哪怕端信誓旦旦说"放行"。

    未污点时才谈授权：授权过就放行（调用方仍要留痕），没授权就问人，问不到人 → 拒。

    **默认全是最严的**（见 `make_confirm_gate` 的默认参数）：新端忘了申报只会更严，不会更松。
    """
    if tainted:
        return ASK if can_ask_human else DENY      # 污点：pre_authorized 一律不作数
    if pre_authorized:
        return ALLOW
    return ASK if can_ask_human else DENY


def make_confirm_gate(
    ask_human: Optional[Callable[[str], Awaitable[bool]]] = None,
    *,
    auto_approve: bool = False,
    can_ask_human: bool = False,
    on_decision: Optional[Callable[[str, bool, bool], Any]] = None,
) -> Callable[[str], Awaitable[bool]]:
    """把 `decide()` 包成工具直接可用的 ``async (message) -> bool`` 确认门。

    端申报两件事（**默认都是最严的**）：``can_ask_human``（问得到人吗）与 ``auto_approve``
    （是否已授权自动放行，如 CLI 的 ``--yes``）。要不要问、能不能免，由 `decide()` 说了算。

    ``on_decision(operation, decision, tainted)``：**每个决定**都回调一次（含自动放行与自动拒绝），
    端拿它打印 / 审计——否则自动放行会静默发生、自动拒绝会不说原因。注意它拿到的是
    **operation（不含警示横幅的原始操作文案）**：端要如实说清"被拒的是哪条命令"就靠它，
    若去取带横幅那份的首行，用户只会看到一大段警示、看不见被拒的究竟是什么。

    **fail-closed 的保证就在这里**（别处不再另设兜底）：
      - `decide()` 在"问不到人"时直接返回 ``DENY``，**压根不会去调 ``ask_human``**——所以端即便
        申报了 ``can_ask_human=False`` 却仍传了个会说 yes 的回调，那个回调的返回值也一律不作数；
      - 万一端申报了 ``can_ask_human=True`` 却没给 ``ask_human``（配置错误），同样拒。

    （历史：这里曾靠一个 ``deny_all`` 哨兵"兜底"，可它的函数体**永远执行不到**——gate 早就返回了，
    docstring 却宣称它是 fail-closed 的兜底。那是个绕着安全机制的维护陷阱，已随本次抽取删除。）
    """
    from src.agents.taint import is_tainted

    def _tell(operation: str, decision: bool, tainted: bool) -> None:
        if on_decision is None:
            return
        try:
            on_decision(operation, decision, tainted)
        except Exception:  # noqa: BLE001 —— 交代/审计失败不该影响决定本身
            pass

    async def gated(message: str) -> bool:
        operation = str(message)                   # 原始操作文案（端展示"拒了什么"用这个）
        tainted = is_tainted()
        verdict = decide(tainted=tainted, pre_authorized=auto_approve,
                         can_ask_human=can_ask_human)
        if verdict == ALLOW:
            _tell(operation, True, tainted)        # 自动放行也要留痕，不能静默
            return True
        if verdict == DENY or ask_human is None:   # 问不到人（或端没给问法）→ 拒
            _tell(operation, False, tainted)
            return False
        prompt = (TAINT_WARNING + operation) if tainted else operation
        ok = bool(await ask_human(prompt))
        _tell(operation, ok, tainted)
        return ok

    return gated
