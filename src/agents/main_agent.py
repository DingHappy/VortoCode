"""VortoCode 主 Agent —— 单一 agent loop（整合 Claude Code / opencode / OpenClaw 的思路）。

设计取舍：
- **一个会话主 agent = LLM + 工具**，跑 ReAct 循环：输入 → 模型 →（工具 → 回灌）* → 最终回复。
  闲聊/提问/读代码这类轻活由主 agent 直接处理 —— 不再需要前置的"意图分类器"。
- **重型开发（dev→test→review）作为一个"工具/子 agent"** 被主 agent 按需调起
  （OpenClaw 的 coding-agent 思路：主 loop 干轻活，重活委派）。
- **plan / build 模式 = 工具权限门**（opencode 的 Plan/Build agent 思路）：plan 只给只读工具，
  写类/重型工具在 plan 下被拒、引导用户切 build —— 落实项目"人在关口"的定位。
- **模型无关**：用"提示式工具协议"（模型输出一个 {"tool","args"} JSON 即调用），
  不依赖具体模型的 function-calling API，任何 OpenAI 兼容 chat 模型都能跑、也便于确定性测试。

本模块只负责"循环 + 协议 + 工具调度"，不依赖 TUI；工具由调用方注入（见 tui/app.py）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path            # 模块级：供 _resolve_within 的返回注解引用（各工厂内仍按需局部导入）
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

# 单个工具结果回灌给模型的最大字符数，避免长输出把上下文撑爆
_MAX_TOOL_RESULT = 4000

# 工具预算用尽时的"收尾"指令：禁用工具、强制据已有上下文给最终回答（而不是丢弃一切返回空）
_FORCE_FINISH_RULE = (
    "\n\n【收尾】工具调用预算已用尽：现在**禁止再调用任何工具**，"
    "直接根据上文已获取的信息给出最终结论/回答；信息不全就基于现有内容尽力总结并点明欠缺，"
    "不要输出任何工具调用 JSON。")

# 对话压缩器的系统提示：把"老段"对话压成滚动纪要，避免长会话里中段决策被硬丢弃。
# 强约束保留原始目标——这正是 #72 锚点想守住的，纪要把它连同关键决策一起守住、且语义化。
_SUMMARY_SYSTEM = (
    "你是对话压缩器。把给定的对话历史压成一段**简洁中文纪要**，务必保留："
    "①用户的原始目标/任务（尽量原话）②已做的关键决策与结论 ③已改动的文件/分支/PR "
    "④尚未完成或待办的事项 ⑤重要约束与踩过的坑。丢弃寒暄与冗余过程细节。"
    "若给了【已有纪要】，把【新增对话】融合进去、输出更新后的**完整**纪要，绝不丢失旧纪要要点。"
    "只输出纪要正文，不要任何前后缀、不要工具调用 JSON。")


@dataclass
class Tool:
    """一个可被主 agent 调用的工具。

    handler 接收解析好的 args(dict)、返回一段字符串结果（会回灌给模型）。
    read_only=True 的工具在 plan 模式下也可用；False（写/重型）仅 build 模式可用。
    """

    name: str
    description: str
    args: dict[str, str]                       # 参数名 -> 说明（仅用于给模型的工具目录）
    handler: Callable[[dict], Awaitable[str]]
    read_only: bool = True


# ----------------------------------------------------------------------- 协议解析
def _strip_fences(text: str) -> str:
    """去掉 ```json ... ``` 围栏。"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _first_json_object(text: str) -> Optional[str]:
    """返回首个"平衡花括号"子串（容忍 JSON 前后夹带杂质/说明文字）。"""
    start = text.find("{")
    if start == -1:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_tool_call(text: str) -> Optional[tuple[str, dict]]:
    """从模型输出解析工具调用，返回 (name, args)；若不是工具调用则 None（当作最终回复）。"""
    body = _strip_fences(text)
    candidate = body if body.startswith("{") and body.endswith("}") else _first_json_object(body)
    if not candidate:
        return None
    try:
        obj = json.loads(candidate)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(obj, dict) and isinstance(obj.get("tool"), str):
        args = obj.get("args")
        return obj["tool"], (args if isinstance(args, dict) else {})
    return None


def parse_tool_calls(text: str) -> Optional[list[tuple[str, dict]]]:
    """解析一个或多个工具调用（仿 Claude Code 的并行工具）：模型可输出单个 {"tool",...}，
    也可输出一个 JSON 数组 [{"tool",...}, ...] 一次调多个（如并行读几个文件）。都不是则 None。"""
    body = _strip_fences(text)
    if body.startswith("[") and body.endswith("]"):     # 数组：一批工具
        try:
            arr = json.loads(body)
        except Exception:  # noqa: BLE001
            arr = None
        if isinstance(arr, list):
            calls = []
            for obj in arr:
                if isinstance(obj, dict) and isinstance(obj.get("tool"), str):
                    a = obj.get("args")
                    calls.append((obj["tool"], a if isinstance(a, dict) else {}))
            if calls:
                return calls
    single = parse_tool_call(text)                       # 退回单个对象
    return [single] if single else None


def _tool_results_msg(results: list) -> str:
    """把一批 (工具名, 结果) 合成一条回灌消息（多工具并行时一次性给回模型）。"""
    return "\n\n".join(f"[工具 {n} 结果]\n{r}" for n, r in results)


def _split_tool_results(text: str, count: int) -> list:
    """把 _tool_results_msg 合成的多结果串按 `[工具 X 结果]` 头拆回每个工具的结果正文（顺序保持）。"""
    parts = re.split(r"(?m)^\[工具 .+? 结果\]\n", text)
    bodies = [p.rstrip("\n") for p in parts if p.strip() != ""]
    return bodies if len(bodies) == count else ([text] + [""] * (count - 1) if count else [])


def _to_native_messages(messages: list) -> list:
    """仿 Claude Code 的消息结构：把提示式历史转成 OpenAI 原生 tool_calls / tool 角色消息，
    只在 native 模式发请求时转换——存储的 history 仍是提示式（跨协议一致，便于裁剪/压缩/持久化）。

    转换规则：assistant 的 {"tool":...}/[...] → assistant.tool_calls；紧跟的 [工具 X 结果] user
    消息 → 每个工具一条 role=tool（带 tool_call_id）。其余消息原样透传。"""
    out, i, n = [], 0, len(messages)
    while i < n:
        m = messages[i]
        content = m.get("content")
        if m.get("role") == "assistant" and isinstance(content, str):
            calls = parse_tool_calls(content)
            if calls:
                tcs = [{"id": f"call_{i}_{j}", "type": "function",
                        "function": {"name": nm, "arguments": json.dumps(a, ensure_ascii=False)}}
                       for j, (nm, a) in enumerate(calls)]
                out.append({"role": "assistant", "content": None, "tool_calls": tcs})
                nxt = messages[i + 1] if i + 1 < n else None
                if (nxt and nxt.get("role") == "user" and isinstance(nxt.get("content"), str)
                        and nxt["content"].startswith("[工具 ")):
                    for tc, res in zip(tcs, _split_tool_results(nxt["content"], len(tcs))):
                        out.append({"role": "tool", "tool_call_id": tc["id"], "content": res})
                    i += 2
                    continue
                i += 1
                continue
        out.append(m)
        i += 1
    return out


# 纠偏：模型既没给有效回答、也没正确调用工具（空收尾 / 残缺工具 JSON 当结论）时，回灌这条让它重来一次
_NUDGE = ("（上一步没有给出有效回答，也没有正确调用工具。请二选一：要么直接用自然语言给出最终回答；"
          "要么严格按协议只输出工具调用 JSON。不要输出残缺的 JSON 或空内容。）")


def _is_weak_final(content: str) -> bool:
    """疑似"没收好尾"：空内容，或看着像想调工具却没解析成（残缺 JSON/围栏）→ 值得纠偏重试一次。"""
    c = (content or "").strip()
    if not c:
        return True
    return c.startswith("{") or c.startswith("[") or c.startswith("```")


def native_default() -> bool:
    """三端统一的 native（原生 function-calling）开关：env `VORTOCODE_NATIVE_TOOLS`。

    **默认开**（2026-07 真机对照 dogfood：同一开发任务 native 3/3 正确落地 vs 提示式仅 1/3——
    提示式下模型易把工具结果误当用户消息、凭空编造交付；native 用结构化 tool 消息从根上避免）。
    模型不支持 function-calling 会**自动永久回退**提示式（见 _native_error_is_permanent + #111），
    所以默认开对不支持的模型也安全（一次失败即回退）。置 0/false/no/off 显式关（强制提示式）。
    三端（TUI/Web/CLI）统一读这里。
    """
    import os
    return os.getenv("VORTOCODE_NATIVE_TOOLS", "1").strip().lower() not in ("0", "false", "no", "off")


def _native_error_is_permanent(exc: BaseException) -> bool:
    """native function-calling 调用出错时，判断是否"永久"（模型/端点根本不支持 tools）——
    只有永久错误才该把该 agent 整个生命周期回退提示式；瞬时错误（超时/连接/5xx/限流）不该。

    旧实现 `except Exception: self._native=False` 把一次瞬时 502/超时也当永久，永久关掉原生工具。
    判据：4xx（除 429）视为永久（请求本身不被接受，如模型不支持 function-calling）；429/5xx/
    超时/连接等视为瞬时；拿不准 → 当瞬时（宁可下轮重试，也别因一次抖动永久关死能力）。
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int):
        return 400 <= status < 500 and status != 429
    msg = str(exc).lower()
    transient = ("timeout", "timed out", "connection", "connect", "temporarily", "reset",
                 "rate limit", "overloaded", "unavailable", "429", "500", "502", "503", "504")
    if any(m in msg for m in transient):
        return False
    permanent = ("400", "404", "422", "unsupported", "not support", "does not support",
                 "no tools", "invalid request", "bad request")
    return any(m in msg for m in permanent)


def _truthy(v: Any) -> bool:
    """宽松真值：兼容原生 function-calling 的 bool 与提示式协议的字符串（"true"/"1"/"yes"…）。"""
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "y", "t", "all")


def _dev_attempts() -> int:
    """隔离实现的最多尝试次数（1 次初始 + 自修复重试）。env VORTOCODE_DEV_ATTEMPTS 调，默认 2、下限 1。"""
    import os
    try:
        return max(1, int(os.getenv("VORTOCODE_DEV_ATTEMPTS") or 2))
    except ValueError:
        return 2


def _dev_parallelism() -> int:
    """并行隔离实现时最多同时在跑的子任务数。env VORTOCODE_DEV_PARALLEL 调，默认 4、下限 1。

    防 dev_auto 分解出很多独立子任务时一次性 fan-out 几十个 worktree+LLM 把中转站/磁盘打爆
    （dev_parallel 另有 [:5] 上限，但 dev_auto 的独立批数量取决于分解结果、原先无界）。
    """
    import os
    try:
        return max(1, int(os.getenv("VORTOCODE_DEV_PARALLEL") or 4))
    except (TypeError, ValueError):
        return 4


def _glob_to_regex(pattern: str) -> "re.Pattern":
    """把含 '/' 的 glob 译成正则（真·glob 语义）：`**/`=任意层目录(含零层)、`**`=跨 / 任意、
    `*`=段内任意(不跨 /)、`?`=段内单字符。比 fnmatch 强在 `src/**/*.ts` 能匹配 src 下任意深度。"""
    i, n, out = 0, len(pattern), []
    while i < n:
        if pattern[i] == "*":
            if pattern[i:i + 3] == "**/":
                out.append(r"(?:.*/)?"); i += 3; continue
            if pattern[i:i + 2] == "**":
                out.append(r".*"); i += 2; continue
            out.append(r"[^/]*"); i += 1; continue
        if pattern[i] == "?":
            out.append(r"[^/]"); i += 1; continue
        out.append(re.escape(pattern[i])); i += 1
    return re.compile("(?s:" + "".join(out) + r")\Z")


def _clip_middle(text: str, limit: int) -> str:
    """超长文本保「头 + 尾」、只省中段，不超过 limit。

    报错/异常栈/测试失败摘要通常在**末尾**——纯头截（text[:limit]）会把它整段丢掉，模型只看到
    冗长的前奏、看不到真正的失败原因，调试/自修复全凭猜。保头是为留住命令与上下文，保尾是为留住结论。
    """
    if len(text) <= limit:
        return text
    head = limit * 2 // 3                 # 头 2/3（上下文）、尾 1/3（结论/报错）
    tail = limit - head
    omitted = len(text) - head - tail
    return f"{text[:head]}\n…(中间省略 {omitted} 字)…\n{text[-tail:]}"


def _repair_prompt(base: str, failure_tail: str) -> str:
    """把上一次的测试失败输出拼回子任务描述，引导下一个全新隔离子 agent 定向修复。"""
    return (f"{base}\n\n【上一次尝试失败】测试未过，失败输出尾部：\n{failure_tail}\n"
            f"请据此定位并修正实现，确保 run_tests 通过。")


def _noop_retry_prompt(base: str) -> str:
    """上一次子 agent 没真正改文件（no-op）→ 下一次用更命令式的提示逼它实际动手。

    推理模型对模糊描述常"只看代码/只跑下已有测试就交差"（diff 0 行）。这里把"必须真改文件"
    说死，配合换全新 worktree 重试，能把命中率从"看运气"拉回来。
    """
    return (f"{base}\n\n【上一次尝试没有产生任何改动】你只是查看或跑了测试，并没有真正实现。"
            f"必须用 edit_file / write_file **实际修改文件**来完成任务，再用 run_tests 自测通过——"
            f"只跑测试而不改文件不算完成。")


def _fmt_args(args: dict) -> str:
    """把工具参数压成一行短串，供 UI 展示。"""
    parts = []
    for k, v in args.items():
        s = str(v).replace("\n", " ")
        parts.append(f"{k}={s[:40] + '…' if len(s) > 40 else s}")
    return ", ".join(parts)


def _tool_catalog(tools: list[Tool]) -> str:
    lines = []
    for t in tools:
        a = "，".join(f"{k}（{desc}）" for k, desc in t.args.items()) or "无"
        kind = "只读" if t.read_only else "写/重型"
        lines.append(f"- {t.name}（{kind}）：{t.description}  参数：{a}")
    return "\n".join(lines)


def _env_block() -> str:
    """仿 Claude Code 的 <env>：给模型注入运行时上下文——工作目录、平台、今天日期、git 分支/改动、
    顶层条目。让模型不再"不知道今天几号、在哪个仓库、哪个分支"。全部 best-effort，取不到就略过。"""
    import os
    import platform
    import subprocess
    from datetime import datetime
    cwd = os.getcwd()
    lines = [f"工作目录: {cwd}", f"平台: {platform.system().lower()}"]
    try:
        lines.append(f"今天: {datetime.now():%Y-%m-%d (%A)}")
    except Exception:  # noqa: BLE001
        pass
    try:
        r = subprocess.run(["git", "-C", cwd, "status", "-b", "--porcelain=v1"],
                           capture_output=True, text=True, timeout=2)
        if r.returncode == 0:
            rows = r.stdout.splitlines()
            branch = ""
            if rows and rows[0].startswith("## "):
                head = rows[0][3:]
                branch = ("HEAD" if head.startswith("HEAD (no branch)")
                          else head.split("...", 1)[0].split(" ", 1)[0])
            changed = sum(1 for x in rows if not x.startswith("## "))
            lines.append(f"git 分支: {branch or '?'}" +
                         (f"（{changed} 处未提交改动）" if changed else "（干净）"))
    except Exception:  # noqa: BLE001 —— 非 git 仓库/无 git：不显示分支
        pass
    try:
        entries = sorted(e + ("/" if os.path.isdir(os.path.join(cwd, e)) else "")
                         for e in os.listdir(cwd) if not e.startswith("."))
        if entries:
            shown = entries[:40]
            more = f" …(+{len(entries) - 40})" if len(entries) > 40 else ""
            lines.append("顶层条目: " + ", ".join(shown) + more)
    except Exception:  # noqa: BLE001
        pass
    return "<env>\n" + "\n".join(lines) + "\n</env>"


SYSTEM_TEMPLATE = """你是 VortoCode 的主助手，在一个终端 TUI 里和用户对话。VortoCode 是一个多 Agent 软件开发框架。
你可以调用下列工具来读代码、扫描仓库，或把"正经的开发任务"交给开发流水线（dev→test→review，会真跑测试与审查）：

{catalog}

【工具调用协议】需要调用工具时，**只输出 JSON、不要任何多余文字**：
- 单个工具：一个对象 {{"tool": "工具名", "args": {{参数...}}}}
- 多个**相互独立的只读**工具想同时用（如一次读几个文件）：一个数组 [{{"tool":...}}, {{"tool":...}}]，
  它们会并行执行、省往返；但有先后依赖、或涉及写/重型操作时，仍一次只给一个。
不需要工具、要回答用户时，**直接输出自然语言**（简洁），不要输出 JSON。
每次工具调用后，其返回会作为**工具输出**回灌给你（提示式协议下以 `[工具 X 结果]` 开头；原生 tools
模式下是结构化 tool 消息）——**那是工具的输出，不是用户发来的新消息**，别把它当成用户在给你看东西
或已经结束对话；据此决定下一步或给出最终回答。若任务需要写文件/跑流水线，**务必真正调用对应工具**
（写文件用 edit_file/write_file，跑开发流水线用上方清单里的相应工具），只读一眼代码不等于完成。

【当前模式】{mode}（{mode_desc}）。{mode_rule}

【原则】寒暄、提问、解释概念、读/找代码这类轻活，直接回答或用只读工具，**不要**动用开发流水线；
只有用户明确要"实现/编写/修改某个具体功能"时，才用**上方工具清单里的开发流水线工具**（隔离实现/
自动分解那类）。你只产出/提议，绝不假装已合并代码；
**更不要声称做了实际没做的事**——没调用过写/dev 工具就没有分支、没有测试、没有改动，绝不编造分支名或测试结果。
汇报时只说工具结果里确有的东西。"""


class MainAgent:
    """主 agent loop。无 UI 依赖：通过 say/emit 回调输出，工具由外部注入。"""

    def __init__(
        self,
        tools: list[Tool],
        llm: Any = None,
        max_steps: int = 6,
        max_history: int = 24,
        max_context_tokens: int = 8000,
        extra_system: Optional[str] = None,
        native: bool = False,
        on_tool: Optional[Callable[[str, dict, str], None]] = None,
        on_escalate: Optional[Callable[[str, dict], Awaitable[bool]]] = None,
        on_plan: Optional[Callable[[list], None]] = None,
        plan_tool: bool = False,
        hook_system: Optional[Any] = None,
        compact: bool = True,
        permissions: Optional[Any] = None,
        env_context: bool = False,
    ) -> None:
        import os
        # 复用既有 src/hooks 的 HookSystem：把工具生命周期事件（pre/post/error）接进 agent loop
        self._hook_system = hook_system
        self._tool_list = list(tools)
        self._llm = llm
        self.max_steps = int(os.getenv("VORTOCODE_MAX_STEPS") or max_steps)   # 可全局调高预算
        # 持久任务清单：大任务的可见/可续骨架。注入系统提示让 agent 始终看得见进度；
        # on_plan 给 UI 渲染。是后续"分解→并行实现→逐件验证"的地基。
        self.plan: list[dict] = []
        self._on_plan = on_plan
        if plan_tool:
            self._tool_list.append(Tool(
                "update_plan",
                "维护任务清单：把当前任务拆成有序步骤并标状态。开始一步标 in_progress、做完标 "
                "completed。工程量大时务必先用它列计划、再逐步推进并更新（每次传完整列表）。",
                {"steps": "步骤列表；每项 {step: 一句话, status: pending|in_progress|completed}"},
                self._update_plan, read_only=True))
        self.tools = {t.name: t for t in self._tool_list}
        # 上下文预算：主要按 **token** 裁剪/压缩（真正决定是否撑爆窗口的是 token，不是消息条数——
        # 少量超大消息条数虽少却能爆窗，大量小消息条数虽多却很省）。max_history 退为**硬条数上限**
        # 兜底（防极端条数），不再作为压缩触发。env VORTOCODE_MAX_CONTEXT_TOKENS 可调。
        self.max_history = max_history
        self.max_context_tokens = int(os.getenv("VORTOCODE_MAX_CONTEXT_TOKENS") or max_context_tokens)
        self._task_anchor = ""                 # 原始任务纯文本（首个 user）；压缩后仍作锚点，修"锚到孤儿工具结果"
        self.extra_system = extra_system       # 追加到系统提示（如技能目录、子 agent 角色）
        self._native = native                  # 原生 function-calling（失败自动回退提示式协议）
        self._on_tool = on_tool                # 工具执行后的审计钩子(name, args, result)
        # plan 模式想用写/重型工具时回调：返回 True=用户同意切 build 并继续，False=拒绝
        self._on_escalate = on_escalate
        self._escalated = False                # 本轮是否已升级到 build（经 on_escalate 同意）
        self.history: list[dict] = []          # 跨轮对话历史（不含 system）
        # 对话压缩：历史超窗时把"老段"摘要成滚动纪要（_summary）注入系统提示，物理移出 history，
        # 而非像 #72 那样硬丢中段。env VORTOCODE_COMPACT=0 关闭（关掉就退回纯锚点裁剪）。
        env_compact = os.getenv("VORTOCODE_COMPACT")
        self.compact = (env_compact not in ("0", "false", "no")) if env_compact is not None else compact
        self._summary = ""                     # 早先轮次的压缩纪要（滚动合并）
        self._permissions = permissions        # 可选 .vortocode/permissions.yaml deny 规则（_run_tool 硬拦）
        self._env_context = env_context        # 仿 CC 注入 <env>（cwd/git/日期/目录）；仅顶层交互 agent 开，子 agent 不开省开销
        self._env = ""                         # 当轮环境快照（run_turn 开始时刷新，_system 注入）

    def _client(self) -> Any:
        # 惰性构建并缓存：跨步/跨轮复用同一个客户端（复用底层连接池），也便于测试注入
        if self._llm is None:
            from src.llm.client import LLMClient
            self._llm = LLMClient()
        return self._llm

    def set_model(self, model: str) -> None:
        """切换本 agent 后续调用使用的模型：就地改客户端 config.model（惰性客户端先建再改）。"""
        self._client().config.model = str(model)

    def current_model(self) -> str:
        """当前 agent 实际会用的模型名（读客户端 config）。拿不到返回空串。"""
        try:
            return self._client().config.model
        except Exception:  # noqa: BLE001
            return ""

    def add_tools(self, tools: list) -> None:
        """运行时追加工具（如连上 MCP 后注入 mcp__* 工具）；同步进 _tool_list（喂系统提示目录）与 tools。"""
        for t in tools:
            self._tool_list.append(t)
            self.tools[t.name] = t

    def _system(self, mode: str) -> str:
        mode_desc = "只读/提案" if mode == "plan" else "可写分支"
        mode_rule = (
            "plan 模式下写/重型工具（如 edit_file / write_file 及各类开发流水线工具）不可用；"
            "若用户想开发，请提示他按 Tab 切到 build 模式。"
            if mode == "plan"
            else "build 模式下所有工具可用。"
        )
        prompt = SYSTEM_TEMPLATE.format(
            catalog=_tool_catalog(self._tool_list),
            mode=mode, mode_desc=mode_desc, mode_rule=mode_rule,
        )
        if self._env:                          # 仿 CC 的 <env>：运行时上下文（cwd/git/日期/目录）
            prompt += "\n\n" + self._env
        hint = self._orchestration_hint()      # 据可用工具给"大任务怎么展开"的编排指引
        if hint:
            prompt += "\n\n" + hint
        if self.extra_system:
            prompt += "\n\n" + self.extra_system
        if self._summary:                      # 早先轮次的压缩纪要：常驻系统提示，最近对话仍在历史里逐字给
            prompt += ("\n\n【对话纪要】(更早轮次的压缩摘要，含原始目标与关键决策；"
                       "最近的对话在下方消息里逐字给出)\n" + self._summary)
        if self.plan:                          # 当前计划常驻系统提示：跨步/跨历史裁剪也不丢
            from src.agents.plan import render_plan
            prompt += ("\n\n【当前计划】(用 update_plan 维护：开始一步标 in_progress、做完标 completed)\n"
                       + render_plan(self.plan))
        return prompt

    def _orchestration_hint(self) -> str:
        """大任务的编排指引：仅当具备相应工具时才给（research 子 agent/orchestrator 无这些工具→不给，
        也就不会被诱导去嵌套/乱用）。让主 agent 把「计划→并行隔离实现→逐件验证」一句话用起来。"""
        has_plan = "update_plan" in self.tools
        has_iso = "dev_isolated" in self.tools
        has_par = "dev_parallel" in self.tools
        has_auto = "dev_auto" in self.tools
        if not (has_plan or has_iso or has_par or has_auto):
            return ""
        lines = ["【怎么干大活】面对多步骤/较大的开发任务，别一上来就埋头改文件，按这个来："]
        if has_plan:
            lines.append("1) 先用 update_plan 把任务拆成有序步骤、列计划；每开始一步标 in_progress、做完标 "
                         "completed，让进度始终可见。")
        if has_auto:
            lines.append("· 懒人路：拿不准怎么拆就直接 dev_auto(task=整个大任务)——它自动分解成独立子任务并并行隔离实现。")
        if has_par or has_iso:
            impl = []
            if has_par:
                impl.append("相互独立的实现步骤用 dev_parallel 一次并行实现")
            if has_iso:
                impl.append("单个步骤用 dev_isolated")
            lines.append("2) 实现优先 " + "、".join(impl) +
                         "——它们在隔离 worktree 里改代码并自动跑测试验证，✅通过才产出可应用的 diff、"
                         "绝不碰主工作区；不要用 edit_file/write_file 在主工作区直接做大改。")
            lines.append("3) ❌未过的块会带失败输出回来，据此修正后只重试那一块。")
        if "open_pr" in self.tools and (has_par or has_iso):
            lines.append("4) 改动落到 vorto/... 分支后，需要的话用 open_pr 把分支推上去并开 PR（外向操作、会确认）。")
        return "\n".join(lines)

    async def _update_plan(self, args: dict) -> str:
        """update_plan 工具：用模型给的步骤列表整体替换当前计划，渲染回灌 + 通知 UI。"""
        from src.agents.plan import normalize_plan, render_plan
        self.plan = normalize_plan(args.get("steps", args))
        if self._on_plan:
            try:
                self._on_plan(self.plan)
            except Exception:  # noqa: BLE001
                pass
        done = sum(1 for p in self.plan if p["status"] == "completed")
        return f"计划已更新（{done}/{len(self.plan)} 完成）：\n{render_plan(self.plan)}"

    def _msg_tokens(self, m: dict) -> int:
        """单条消息的粗略 token 数：只算文本（content_to_text 去掉图/音 base64）+ 少量角色开销。"""
        from src.llm.content import content_to_text
        from src.llm.client import estimate_tokens
        return estimate_tokens(content_to_text(m.get("content"))) + 4

    def _anchor_text(self) -> str:
        """原始任务纯文本：优先用捕获的 _task_anchor（压缩后仍在），否则回退扫历史首个 user。"""
        if self._task_anchor:
            return self._task_anchor
        from src.llm.content import content_to_text
        fu = next((m for m in self.history if m.get("role") == "user"), None)
        return content_to_text(fu.get("content")) if fu else ""

    def _is_anchor_msg(self, m: Optional[dict], anchor: str) -> bool:
        """m 是否就是锚点本身（原始 user 且纯文本一致）——避免把锚点重复塞一遍。"""
        if not m or m.get("role") != "user":
            return False
        from src.llm.content import content_to_text
        return content_to_text(m.get("content")) == anchor

    def _trimmed_history(self) -> list[dict]:
        """按 **token 预算** 裁剪跨轮历史（max_history 仅作硬条数上限兜底）。

        为什么按 token 而非条数：真正撑爆上下文窗口的是 token——少量超大消息（一段 8000 字的
        read_file、注入的 @上下文）条数虽 <max_history 却能爆窗；大量小消息条数虽多却很省。
        裁剪时不裸取尾部——那样会把**原始任务**静默丢掉。始终把原始任务（_anchor_text，纯文本、
        去 base64）当锚点置顶 + 从最近往前收进 token 预算的窗口。
        """
        h = self.history
        total = sum(self._msg_tokens(m) for m in h)
        if len(h) <= self.max_history and total <= self.max_context_tokens:
            return list(h)                                 # 未超条数也未超 token 预算 → 原样（短对话零改动）
        anchor = self._anchor_text()
        limit_n = max(1, self.max_history - (1 if anchor else 0))   # 给锚点留 1 条，总数仍 ≤ max_history
        kept: list[dict] = []
        used = 0
        for m in reversed(h):                              # 从最近往前收，受 token 预算 + 条数上限双约束
            t = self._msg_tokens(m)
            if kept and (used + t > self.max_context_tokens or len(kept) >= limit_n):
                break
            kept.append(m)
            used += t
        kept.reverse()
        if anchor and not self._is_anchor_msg(kept[0] if kept else None, anchor):
            kept = [{"role": "user", "content": anchor}] + kept
        return kept

    async def _maybe_compact(self, say: Callable[[str], None]) -> None:
        """历史 **token 数** 超预算时，把"老段"摘要成滚动纪要、物理移出 history（保留最近窗口逐字）。

        在回合开始时调一次（跨轮增长在此收口；单轮内的 max_steps 增长由 _trimmed_history 兜底）。
        按 token 触发（而非条数）：大量小消息不会白白触发一次 LLM 摘要；少量超大消息则会及时压。
        摘要失败/无 LLM 都安全跳过 —— 历史原样保留，下游 _trimmed_history 仍按锚点裁剪，纯降级。
        关闭压缩（compact=False）时直接返回。
        """
        if not self.compact:
            return
        h = self.history
        total = sum(self._msg_tokens(m) for m in h)
        if total <= self.max_context_tokens:   # 没超 token 预算就不折腾（短对话/小消息零开销、零 LLM 调用）
            return
        half = max(1, self.max_context_tokens // 2)   # 最近约半预算逐字保留；更早的老段压成纪要
        used, cut = 0, 0
        for i in range(len(h) - 1, -1, -1):    # 从最近往前累计，越过半预算处即为切点
            used += self._msg_tokens(h[i])
            if used > half:
                cut = i + 1
                break
        # recent 必须至少保留**当前轮最新 user**（h[-1]）：run_turn 刚把本轮用户请求追加到末尾，
        # 若它单独就超半预算，上面的 cut 会等于 len(h) → recent 空 → 本轮请求被整体划进 older 只喂给
        # 摘要器，主模型收不到原文细节。钳住 cut ≤ len(h)-1，保证本轮请求始终逐字进主模型上下文。
        cut = min(cut, len(h) - 1)
        older, recent = h[:cut], h[cut:]
        if not older:                          # 无老段可压（如历史仅当前轮）：交给 _trimmed_history 兜底
            return
        digest = await self._summarize(older)
        if not digest:                         # 摘要失败：保持原历史，安全降级（不丢消息、不阻塞回合）
            return
        self._summary = digest                 # 含已有纪要的滚动合并（在 _summarize 内拼）
        self.history = recent
        say(f"[dim]🗜️ 已把 {len(older)} 条更早的对话压成纪要（保留原始目标与关键决策）。[/dim]")

    async def _summarize(self, msgs: list[dict]) -> str:
        """把一段历史消息（+ 已有纪要）交给 LLM 压成更新后的纪要；任何异常都返回空串（让上游降级）。"""
        from src.llm.content import content_to_text
        convo = "\n".join(
            f"{m.get('role', '?')}: {content_to_text(m.get('content'))[:1500]}" for m in msgs)
        user = (f"【已有纪要】\n{self._summary}\n\n" if self._summary else "") + \
               f"【新增对话】\n{convo}\n\n请输出更新后的完整纪要。"
        prompt = [{"role": "system", "content": _SUMMARY_SYSTEM},
                  {"role": "user", "content": user}]
        try:
            resp = await self._client().chat(prompt, temperature=0.2)
        except Exception:  # noqa: BLE001
            return ""
        return (resp.get("content") or "").strip()[:2000]   # 纪要本身也设上限，防越滚越大

    async def _complete(self, messages: list[dict], stream_cb: Optional[Callable[[str], None]],
                        reasoning_cb: Optional[Callable[[str], None]] = None) -> str:
        """取一步模型输出。

        给了 stream_cb 且客户端支持流式 → 边生成边回显；但**疑似工具调用**（首个非空字符是
        `{` 或 ``` ）则静默缓冲、不把原始 JSON 流给 UI。否则退回一次性 chat（也便于测试）。
        reasoning_cb：推理型模型的思维链（reasoning_content）走它做"思考呈现"，与正文分开。
        """
        client = self._client()
        if stream_cb is None or not hasattr(client, "stream"):
            resp = await client.chat(messages, temperature=0.3)
            if reasoning_cb is not None and resp.get("reasoning"):   # 非流式：整段思维链一次性给
                try:
                    reasoning_cb(str(resp["reasoning"]))
                except Exception:  # noqa: BLE001
                    pass
            return resp.get("content") or ""
        buf: list[str] = []
        show: Optional[bool] = None        # None=未定；True=显示；False=抑制(疑似工具调用)
        try:
            gen = client.stream(messages, temperature=0.3, on_reasoning=reasoning_cb)
        except TypeError:                  # 老客户端/假 LLM 不认 on_reasoning：退回不带它
            gen = client.stream(messages, temperature=0.3)
        async for tok in gen:
            buf.append(tok)
            if show is None:
                head = "".join(buf).lstrip()
                if head:
                    show = not (head.startswith("{") or head.startswith("```"))
            if show:
                stream_cb("".join(buf))
        return "".join(buf)

    async def _native_complete(self, native_msgs: list, schema: list,
                               stream_cb: Optional[Callable[[str], None]],
                               reasoning_cb: Optional[Callable[[str], None]]) -> tuple[dict, bool]:
        """原生 function-calling 取一步。返回 (resp, streamed)。

        给了 stream_cb 且客户端支持 stream_chat → 流式：最终正文边生成边累计回显（stream_cb 收
        **累计**文本，与提示式 _complete 一致）；工具调用响应静默累积、不回显碎语。否则退回一次性
        chat（也便于测试的假 LLM）。异常交由调用方处理（永久错误→回退提示式）。streamed=True 时思维链
        已过 on_reasoning 增量给出，调用方别再整段重放。"""
        client = self._client()
        if stream_cb is not None and hasattr(client, "stream_chat"):
            buf: list[str] = []

            def _on_content(delta: str) -> None:
                buf.append(delta)
                stream_cb("".join(buf))            # 累计文本（三端 stream_cb 契约）

            resp = await client.stream_chat(
                native_msgs, temperature=0.3, tools=schema,
                on_content=_on_content, on_reasoning=reasoning_cb)
            return resp, True
        return await client.chat(native_msgs, temperature=0.3, tools=schema), False

    def _tools_schema(self) -> list[dict]:
        """把工具表转成 OpenAI function-calling 的 schema（原生工具模式用）。"""
        schema = []
        for t in self._tool_list:
            props = {k: {"type": "string", "description": v} for k, v in t.args.items()}
            schema.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": {"type": "object", "properties": props, "required": []},
                },
            })
        return schema

    async def _run_tool(self, name: str, args: dict, mode: str,
                        say: Callable[[str], None]) -> str:
        """按权限门执行一个工具，返回（截断后的）结果字符串。两种协议共用。"""
        tool = self.tools.get(name)
        if tool is None:
            return f"没有名为 {name} 的工具。可用：{', '.join(self.tools)}"
        if self._permissions is not None:           # .vortocode/permissions.yaml deny：硬拦（不分模式、最优先）
            reason = self._permissions.denied(name, args)
            if reason:
                say(f"🔧 [b]{name}[/b][dim] —— 被权限规则拦下[/dim]")
                return f"[权限拦截] {reason}"
        effective = "build" if self._escalated else mode
        if effective == "plan" and not tool.read_only:
            # plan 想用写/重型工具：有 on_escalate 就问用户"切 build 并继续？"；同意则升级执行。
            ok = False
            if self._on_escalate is not None:
                try:
                    ok = await self._on_escalate(name, args)
                except Exception:  # noqa: BLE001
                    ok = False
            if not ok:
                return (f"工具 {name} 在 plan 模式下不可用（只读/提案）。"
                        f"如需执行请切到 build 模式（Tab）。")
            self._escalated = True
        if self._hook_system is not None:       # PRE_TOOL_USE：钩子可阻止该工具（should_stop）
            block = await self._fire_hook("pre_tool_use", {"tool": name, "args": args}, stoppable=True)
            if block is not None:
                say(f"🔧 [b]{name}[/b][dim] —— 被 hook 阻止[/dim]")
                return block
        say(f"🔧 [b]{name}[/b][dim] {_fmt_args(args)}[/dim]")
        try:
            result = str(await tool.handler(args))
        except Exception as e:  # noqa: BLE001
            result = f"工具 {name} 执行出错: {e}"
            await self._fire_hook("tool_error", {"tool": name, "args": args, "error": str(e)})
        result = _clip_middle(result, _MAX_TOOL_RESULT)   # 超长保头+尾：别把末尾的报错/失败摘要截没了
        # POST_TOOL_USE：钩子据此做后处理（如自动格式化）；message 附在结果后
        post = await self._fire_hook("post_tool_use", {"tool": name, "args": args, "result": result})
        if post:
            result = result + "\n[hook] " + post
        if self._on_tool is not None:           # 审计钩子（失败不影响工具）
            try:
                self._on_tool(name, args, result)
            except Exception:  # noqa: BLE001
                pass
        return result

    async def _run_tools(self, calls: list, mode: str,
                         say: Callable[[str], None]) -> list:
        """执行一批工具调用（仿 CC 并行）：全是只读 → 并发跑；含写/重型 → 顺序跑（写工具有
        确认弹窗、不能并发）。返回 [(工具名, 结果字符串)]，顺序与 calls 一致。单个调用直接跑。"""
        import asyncio
        if len(calls) == 1:
            n, a = calls[0]
            return [(n, await self._run_tool(n, a, mode, say))]
        all_ro = all(self.tools.get(n) is not None and self.tools[n].read_only for n, _ in calls)
        if all_ro:                                  # 全只读 → 并发（CC 式并行读）
            rs = await asyncio.gather(*[self._run_tool(n, a, mode, say) for n, a in calls],
                                      return_exceptions=True)
            return [(n, (r if not isinstance(r, BaseException) else f"(工具出错: {r})"))
                    for (n, _), r in zip(calls, rs)]
        out = []                                    # 含写/重型 → 顺序（确认 UI 不能并发、写有先后）
        for n, a in calls:
            out.append((n, await self._run_tool(n, a, mode, say)))
        return out

    async def _fire_hook(self, event_name: str, data: dict, stoppable: bool = False) -> Optional[str]:
        """触发一个工具生命周期钩子事件（复用 src/hooks 的 HookSystem）。

        stoppable=True（pre）：若任一钩子 should_stop → 返回阻止消息（非空即拦下工具）；否则 None。
        stoppable=False（post/error）：返回各钩子 message 的拼接（供 post 附在结果后），无则 None。
        无钩子系统 / 触发出错都安全返回 None（钩子绝不该让工具链崩）。
        """
        if self._hook_system is None:
            return None
        try:
            from src.hooks import HookEventType
            r = await self._hook_system.trigger(HookEventType(event_name), source="main_agent", data=data)
        except Exception:  # noqa: BLE001
            return None
        msgs = [x["result"].message for x in getattr(r, "results", [])
                if x.get("result") is not None and getattr(x["result"], "message", None)]
        if stoppable:
            if getattr(r, "should_stop", False):
                return f"[hook 阻止 {data.get('tool')}] " + ("；".join(msgs) if msgs else "(无说明)")
            return None
        return "；".join(msgs) if msgs else None

    async def run_turn(
        self,
        user_text: str,
        mode: str = "plan",
        say: Optional[Callable[[str], None]] = None,
        emit: Optional[Callable[[str], None]] = None,
        stream_cb: Optional[Callable[[str], None]] = None,
        images: Optional[list] = None,
        audio: Optional[list] = None,
        reasoning_cb: Optional[Callable[[str], None]] = None,
    ) -> str:
        """一轮对话的外壳：在回合首尾触发 agent_start / agent_end 生命周期钩子，主体见 _run_turn_body。

        这两个钩子 + 工具级 pre/post_tool_use/tool_error（见 _run_tool）让外部消费者（桌宠/状态栏/
        通知）能**只靠 hooks** 拿到完整动作状态——UI 无关，TUI/CLI/Web 三端都触发。无 hook_system 时
        _fire_hook 立即返回、零开销（子 agent 默认无 hook_system，故不会刷状态）。
        reasoning_cb：可选——推理型模型的思维链（reasoning_content）走它做"思考呈现"，与正文分开。
        """
        await self._fire_hook("agent_start", {"text": str(user_text)[:500], "mode": mode})
        try:
            return await self._run_turn_body(
                user_text, mode=mode, say=say, emit=emit,
                stream_cb=stream_cb, images=images, audio=audio, reasoning_cb=reasoning_cb)
        finally:
            await self._fire_hook("agent_end", {"mode": mode})

    async def _run_turn_body(
        self,
        user_text: str,
        mode: str = "plan",
        say: Optional[Callable[[str], None]] = None,
        emit: Optional[Callable[[str], None]] = None,
        stream_cb: Optional[Callable[[str], None]] = None,
        images: Optional[list] = None,
        audio: Optional[list] = None,
        reasoning_cb: Optional[Callable[[str], None]] = None,
    ) -> str:
        """处理一轮用户输入：循环"模型↔工具"，最终把回复交给 emit；并返回最终回复文本。

        say(markup): UI 提示行（如"🔧 调用工具"）；emit(text): 最终/工具的字面输出。
        stream_cb(partial): 给了就把"最终回复"边生成边流式回显（疑似工具调用的 JSON 不显示）。
        images: 可选图片引用列表（本地路径/URL/data URL）。
        audio:  可选音频引用列表（本地路径/data URL）——mimo-v2.5 可直接听音频(input_audio)。
                有图/音则本轮 user 消息变多模态内容块；都没有则保持纯字符串、完全向后兼容。
        返回值：最终回复文本（供子 agent 把结论交回父 agent）。
        """
        say = say or (lambda _m: None)
        emit = emit or (lambda _m: None)
        self._escalated = False                # 每轮重置；切 build 由 UI 持久化到 mode
        from src.llm.content import build_user_content
        self.history.append({"role": "user",
                             "content": build_user_content(user_text, images, audio)})
        if not self._task_anchor:              # 捕获原始任务（首个 user 纯文本）——压缩后仍作锚点（修 #16）
            self._task_anchor = self._anchor_text()
        await self._maybe_compact(say)         # 跨轮历史超 token 预算→把老段摘要成纪要（失败安全降级）
        if self._env_context:                  # 仿 CC：每轮刷新一次运行时环境（cwd/git/日期/目录）注入系统提示
            self._env = _env_block()

        nudged = False                         # 本轮是否已纠偏过一次（空收尾/残缺工具 JSON → 只重试一次）
        for _step in range(self.max_steps):
            messages = [{"role": "system", "content": self._system(mode)}] + self._trimmed_history()

            # 原生 function-calling 路径（opt-in）；模型不支持就永久回退到提示式协议
            if self._native:
                streamed = False
                try:
                    # 结构化 tool_use/tool_result：把提示式历史转成原生 tool_calls/tool 消息再发。
                    # 给了 stream_cb → 流式回显最终回复（修：#116 翻默认后 native 曾丢失流式输出）。
                    resp, streamed = await self._native_complete(
                        _to_native_messages(messages), self._tools_schema(), stream_cb, reasoning_cb)
                except Exception as e:  # noqa: BLE001
                    # 只有"模型不支持 tools"这类永久错误才永久回退提示式；瞬时错误（超时/5xx/限流）
                    # 保留 native、下轮重试。本步无论如何走下面的提示式协议兜底，回合照常推进。
                    if _native_error_is_permanent(e):
                        self._native = False
                    resp = None
                if resp is not None:
                    # 流式时思维链已过 on_reasoning 增量给出；非流式才整段重放一次（避免重复）
                    if not streamed and reasoning_cb is not None and resp.get("reasoning"):
                        try:
                            reasoning_cb(str(resp["reasoning"]))
                        except Exception:  # noqa: BLE001
                            pass
                    tcs = resp.get("tool_calls") or []
                    content = (resp.get("content") or "").strip()
                    calls = []                     # 执行模型给出的**全部** tool_calls（CC 式并行，不再只取第一个）
                    for tc in tcs:
                        try:
                            a = json.loads(tc.get("arguments") or "{}")
                        except Exception:  # noqa: BLE001
                            a = {}
                        calls.append((tc.get("name", ""), a if isinstance(a, dict) else {}))
                    if not calls:                  # 最终回复
                        if not nudged and _is_weak_final(content):   # 空收尾 → 纠偏重试一次
                            nudged = True
                            self.history.append({"role": "user", "content": _NUDGE})
                            continue
                        self.history.append({"role": "assistant", "content": content})
                        emit(content or "(无回复)")
                        return content
                    # 用提示式历史表示这一步（简单稳健、跨协议一致、便于裁剪/持久化）
                    self.history.append({"role": "assistant", "content": json.dumps(
                        [{"tool": n, "args": a} for n, a in calls], ensure_ascii=False)})
                    results = await self._run_tools(calls, mode, say)
                    self.history.append({"role": "user", "content": _tool_results_msg(results)})
                    continue

            # 提示式协议（默认；也是 native 回退后的路径）
            try:
                content = (await self._complete(messages, stream_cb, reasoning_cb)).strip()
            except Exception as e:  # noqa: BLE001
                # 有些异常 str 为空（如 5xx），给类型+折行截断的 detail 才可诊断（如 502 Bad Gateway）
                detail = " ".join((str(e) or repr(e)).split())[:200]
                hint = "（多为中转站/网络/额度问题，稍后重试或检查 OPENAI_API_BASE/KEY）" \
                    if any(k in detail for k in ("502", "503", "504", "Bad Gateway",
                                                 "Connection", "Timeout", "timed out")) else ""
                emit(f"对话出错: {type(e).__name__}: {detail}{hint}")
                return ""
            calls = parse_tool_calls(content)      # 单个或一批（JSON 数组）工具调用
            if not calls:                          # 最终回复
                if not nudged and _is_weak_final(content):   # 空/残缺工具 JSON 当结论 → 纠偏重试一次
                    nudged = True
                    self.history.append({"role": "assistant", "content": content or ""})
                    self.history.append({"role": "user", "content": _NUDGE})
                    continue
                self.history.append({"role": "assistant", "content": content})
                emit(content or "(无回复)")
                return content
            self.history.append({"role": "assistant", "content": content})
            results = await self._run_tools(calls, mode, say)   # 全只读→并发；含写→顺序
            self.history.append({"role": "user", "content": _tool_results_msg(results)})

        # 用尽工具预算：不白跑——强制一次"无工具"收尾，把已收集的信息综合成最终回答
        # （子 agent 尤其受益：读了一堆文件也能交回结论，而不是返回空丢弃全部上下文）。
        return await self._force_finish(mode, stream_cb, emit, reasoning_cb)

    async def _force_finish(self, mode: str,
                            stream_cb: Optional[Callable[[str], None]],
                            emit: Callable[[str], None],
                            reasoning_cb: Optional[Callable[[str], None]] = None) -> str:
        """工具预算用尽后的收尾：禁用工具、强制据已有上下文给最终回答，避免丢弃全部工作。"""
        messages = [{"role": "system", "content": self._system(mode) + _FORCE_FINISH_RULE}] \
            + self._trimmed_history()
        try:
            content = (await self._complete(messages, stream_cb, reasoning_cb)).strip()
        except Exception:  # noqa: BLE001
            emit("（已达工具调用上限；收尾时网络/中转站出错，请稍后重试或换种说法。）")
            return ""
        if parse_tool_call(content) is not None:    # 模型仍想调工具：放弃，给降级提示
            content = ""
        self.history.append({"role": "assistant", "content": content or "(无回复)"})
        emit(content or "（已达工具调用上限，且未能据已有信息收尾；可换种说法，或用 /run 直接开发。）")
        return content


@dataclass
class LoadedSkill:
    """一个已加载的技能：元数据 + 正文指令（progressive disclosure 的"完整指令"部分）。"""

    name: str
    description: str
    instructions: str


class SkillRegistry:
    """SKILL.md 技能注册表（复用 src.skills.parser.SkillParser；整合 OpenClaw/Claude Code 的技能思路）。

    渐进式披露：只把 name+description 放进系统提示（catalog），完整正文等 use_skill 时才加载。
    """

    def __init__(self, skill_dirs: list[str]) -> None:
        self._dirs = list(skill_dirs)
        self.skills: dict[str, LoadedSkill] = {}

    def load(self) -> "SkillRegistry":
        """扫描各技能目录、解析 SKILL.md。解析失败/目录损坏都安全跳过，不影响 agent。"""
        self.skills.clear()
        try:
            from src.skills.parser import SkillParser
        except Exception:  # noqa: BLE001
            return self
        for path in SkillParser.discover_skills(self._dirs):
            try:
                meta, instructions, _args, _hooks = SkillParser.parse_file(path)
            except Exception:  # noqa: BLE001
                continue
            name = (meta.name or path.parent.name).strip()
            if name:
                self.skills[name] = LoadedSkill(name, (meta.description or "").strip(), instructions)
        return self

    def catalog(self) -> str:
        """给系统提示用的技能清单（仅 name+description）。无技能则空串。"""
        return "\n".join(f"- {s.name}：{s.description}" for s in self.skills.values())

    def get(self, name: str) -> Optional[LoadedSkill]:
        return self.skills.get((name or "").strip())


def _resolve_within(base: Any, rel: Any) -> Optional["Path"]:
    """把相对路径解析进 base 并做边界校验：`..` 越界、绝对路径、软链逃逸都返回 None。

    读工具与写工具**共用同一套防护**——避免"写路径防了、读路径没防"的不对称
    （否则 read_file("../../etc/passwd") 或绝对路径能读仓库外任意文件）。
    用 resolve() 后 relative_to(base) 判定：绝对路径会被 `base / "/x"` 语义丢掉 base
    → 落到根 → relative_to 抛 ValueError；`..` 与软链在 resolve() 后同样落到 base 外被拒。
    """
    from pathlib import Path
    rel = str(rel or "").strip().lstrip("@")
    if not rel:
        return None
    try:
        root = Path(base).resolve()
        p = (root / rel).resolve()
        p.relative_to(root)
    except (ValueError, OSError):
        return None
    return p


def build_read_tools(repo_root: str) -> list[Tool]:
    """构建一组只读工具（read_file/list_files/grep/analyze_repo），UI 无关，供 TUI/Web 共用。"""
    from pathlib import Path

    # 全仓库文本文件（grep/list_files 用）：跳过噪音目录、只收文本类扩展名——
    # 之前只扫 src/tests 的 .py，搜不到 web/html、examples、docs、配置等，是多语言仓库的大盲区。
    _SKIP_DIRS = {".git", "node_modules", "__pycache__", ".vortocode", ".venv", "venv",
                  "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                  ".idea", ".vscode", "htmlcov", ".eggs", "site-packages"}
    _TEXT_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".md", ".rst",
                 ".yaml", ".yml", ".toml", ".json", ".txt", ".cfg", ".ini", ".sh", ".sql", ".env"}

    def _files() -> list[str]:
        import os
        base = Path(repo_root)
        out: list[str] = []
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]   # 原地剪枝：不下钻噪音目录
            for fn in files:
                if Path(fn).suffix.lower() in _TEXT_EXT:
                    out.append(str((Path(root) / fn).relative_to(base)))
                    if len(out) >= 4000:
                        return sorted(out)
        return sorted(out)

    def _int(v):
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            return None

    async def _read_file(args: dict) -> str:
        rel = str(args.get("path", "")).strip().lstrip("@")
        if not rel:
            return "缺少 path 参数。"
        p = _resolve_within(repo_root, rel)
        if p is None:
            return f"路径越界或非法（只能读仓库内文件）: {rel}"
        if not p.is_file():
            return f"(不存在: {rel})"
        try:
            text = p.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            return f"读取失败: {e}"
        start = _int(args.get("start"))
        # 给了 start：读指定行段（1-based，含端点）——接上 find_definition/document_symbols 给的行号
        if start is not None:
            lines = text.splitlines()
            s = max(1, start)
            end = _int(args.get("end"))
            e = min(len(lines), end if (end is not None and end >= s) else s + 120)   # 默认约 120 行
            chunk = "\n".join(lines[s - 1:e])[:8000]
            return f"# {rel} 第 {s}–{e} 行（共 {len(lines)} 行）\n{chunk}"
        # 无 start：整文件；超长截断并提示用 start/end 读指定行段（别只能看开头）
        if len(text) > 6000:
            total = text.count("\n") + 1
            return (f"# {rel}（共 {total} 行，过长，仅显示前部；用 start/end 读指定行段）\n"
                    f"{text[:6000]}\n…(已截断，用 read_file(path, start, end) 读更后面)")
        return text

    async def _list_files(args: dict) -> str:
        sub = str(args.get("dir", "")).strip().strip("/")
        fs = [f for f in _files() if f.startswith(sub)] if sub else _files()
        return "\n".join(fs[:200]) if fs else "(无源码文件)"

    async def _glob(args: dict) -> str:
        """按文件名 glob 找文件（对标 CC 的 Glob）：不限文本扩展名、跳噪音目录、按最近修改排序。

        pattern 不含 '/' → 匹配**文件名**（最常用，如 `*.ts`/`*.test.js`/`conftest.py`，任意深度）；
        含 '/' → 匹配相对路径全程（`**` 为 best-effort）。可选 dir 限定子目录。
        """
        import fnmatch
        import os
        pattern = str(args.get("pattern", "")).strip()
        if not pattern:
            return "glob 需要 pattern（如 *.ts、**/*.test.js、src/**/*.py）。"
        sub = str(args.get("dir", "")).strip().strip("/")
        base = Path(repo_root)
        if sub and _resolve_within(repo_root, sub) is None:   # dir 不得指向仓库外（防 os.walk 逃逸）
            return f"dir 越界或非法（只能在仓库内查找）: {sub}"
        slash_re = _glob_to_regex(pattern) if "/" in pattern else None

        def _match(rel_posix: str) -> bool:
            if slash_re is not None:                     # 含 / → 全路径真·glob（** 跨目录）
                return slash_re.match(rel_posix) is not None
            return fnmatch.fnmatch(os.path.basename(rel_posix), pattern)   # 否则匹配文件名、任意深度

        hits: list[tuple[float, str]] = []
        for root, dirs, files in os.walk(base / sub if sub else base):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fn in files:
                fp = Path(root) / fn
                try:
                    rel = str(fp.relative_to(base))
                except ValueError:
                    continue
                if _match(rel.replace(os.sep, "/")):
                    try:
                        mtime = fp.stat().st_mtime
                    except OSError:
                        mtime = 0.0
                    hits.append((mtime, rel))
                    if len(hits) >= 5000:                # 防超大仓走查爆内存
                        break
        if not hits:
            return f"没有匹配 `{pattern}` 的文件{('（在 ' + sub + '/ 下）') if sub else ''}。"
        hits.sort(key=lambda t: t[0], reverse=True)      # 最近修改的排前（像 CC，方便找刚动过的）
        shown = [r for _m, r in hits[:200]]
        tail = f"\n…(共 {len(hits)} 个，只列最近 200)" if len(hits) > 200 else ""
        return "\n".join(shown) + tail

    async def _grep(args: dict) -> str:
        pat = str(args.get("pattern", "")).strip()
        if not pat:
            return "缺少 pattern 参数。"
        try:
            rx = re.compile(pat)
        except re.error as e:
            return f"无效正则: {e}"
        sub = str(args.get("dir", "")).strip().strip("/")        # 此前 dir 被宣传却没生效→在此兜上
        files = [f for f in _files() if f.startswith(sub)] if sub else _files()
        ctx = max(0, min(_int(args.get("context")) or 0, 5))     # 上下文行数（±N），上限 5 防输出爆炸
        base = Path(repo_root)

        if ctx == 0:                                             # 紧凑模式（默认）：path:line: text 单行命中
            hits: list[str] = []
            for f in files:
                try:
                    lines = (base / f).read_text(encoding="utf-8", errors="ignore").splitlines()
                except Exception:  # noqa: BLE001
                    continue
                for i, line in enumerate(lines, 1):
                    if rx.search(line):
                        hits.append(f"{f}:{i}: {line.strip()[:200]}")
                        if len(hits) >= 100:
                            return "\n".join(hits)
            return "\n".join(hits) if hits else f"没有匹配 /{pat}/ 的内容。"

        # context > 0：按文件分组、合并相邻窗口、标出命中行（> 前缀），类似 ripgrep -C，定位后不必再 read_file
        out: list[str] = []
        for f in files:
            try:
                lines = (base / f).read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception:  # noqa: BLE001
                continue
            matches = [i for i, line in enumerate(lines) if rx.search(line)]   # 0-based 命中行
            if not matches:
                continue
            ranges: list[list[int]] = []                         # 把每个命中的 ±ctx 窗口合并、相邻即并
            for m in matches:
                lo, hi = max(0, m - ctx), min(len(lines) - 1, m + ctx)
                if ranges and lo <= ranges[-1][1] + 1:
                    ranges[-1][1] = max(ranges[-1][1], hi)
                else:
                    ranges.append([lo, hi])
            mset = set(matches)
            out.append(f"{f}:")
            for ri, (lo, hi) in enumerate(ranges):
                if ri:
                    out.append("   ⋯")                           # 同文件内不连续窗口的分隔
                for ln in range(lo, hi + 1):
                    mark = ">" if ln in mset else " "
                    out.append(f"{ln + 1:>5} {mark} {lines[ln][:200]}")
            out.append("")
            if sum(len(x) for x in out) > 6000:                  # 总输出兜底，防爆
                out.append("…(结果较多，已截断；缩小 pattern、给 dir 或调小 context)")
                break
        return "\n".join(out).rstrip() if out else f"没有匹配 /{pat}/ 的内容。"

    async def _analyze_repo(args: dict) -> str:
        from src.orchestrator.self_analysis import analyze_self, render_report
        return render_report(await analyze_self(repo_root))

    async def _find_definition(args: dict) -> str:
        from src.agents.lsp import find_definition
        return find_definition(repo_root, str(args.get("symbol", "")))

    async def _find_references(args: dict) -> str:
        from src.agents.lsp import find_references
        return find_references(repo_root, str(args.get("symbol", "")))

    async def _document_symbols(args: dict) -> str:
        from src.agents.lsp import document_symbols
        rel = str(args.get("path", "")).strip().lstrip("@")
        if rel and _resolve_within(repo_root, rel) is None:   # 与 read_file 同一边界防护
            return f"路径越界或非法（只能读仓库内文件）: {rel}"
        return document_symbols(repo_root, rel)

    def _git_ro(*a):
        """只读 git：在 repo_root 跑，超时/出错都安全返回 CompletedProcess-ish。"""
        import subprocess
        return subprocess.run(["git", "-C", repo_root, *a], capture_output=True, text=True, timeout=20)

    async def _git_status(args: dict) -> str:
        try:
            r = _git_ro("status", "--short", "--branch")
        except Exception as e:  # noqa: BLE001
            return f"git status 失败: {e}（不是 git 仓库？）"
        out = (r.stdout or "").strip()
        lines = [ln for ln in out.splitlines() if ln.strip()]
        # `--branch` 总会带一行 `## <branch>`；没有文件改动行 = 干净
        files = [ln for ln in lines if not ln.startswith("##")]
        if not files:
            head = lines[0] if lines else "## (无分支)"
            return f"{head}  —— 工作区干净，无未提交改动"
        return out

    async def _list_branches(args: dict) -> str:
        """列本地分支（按最近提交排序，标注当前分支）——尤其方便看 dev_* 产出的 vorto/* 分支。"""
        try:
            r = _git_ro("for-each-ref", "--sort=-committerdate", "--count=40",
                        "--format=%(refname:short)\t%(committerdate:relative)\t%(subject)",
                        "refs/heads/")
            cur = _git_ro("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        except Exception as e:  # noqa: BLE001
            return f"git 列分支失败: {e}（不是 git 仓库？）"
        rows = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        if not rows:
            return "(没有本地分支)"
        out = ["本地分支（按最近提交排序）："]
        for ln in rows:
            parts = ln.split("\t")
            name = parts[0]
            when = parts[1] if len(parts) > 1 else ""
            subj = parts[2] if len(parts) > 2 else ""
            mark = "* " if name == cur else "  "
            out.append(f"{mark}{name}  ({when}) {subj[:60]}")
        return "\n".join(out)

    async def _show_diff(args: dict) -> str:
        ref = str(args.get("ref") or "").strip()
        extra = ref.split() if ref else []          # ref 作为 git 参数透传（只读、无 shell 注入）
        try:
            stat = _git_ro("diff", "--stat", *extra)
            full = _git_ro("diff", *extra)
        except Exception as e:  # noqa: BLE001
            return f"git diff 失败: {e}"
        if stat.returncode != 0:
            return f"git diff 出错（ref 无效？）: {(stat.stderr or '').strip()[:200]}"
        diff = full.stdout or ""
        if not diff.strip():
            return f"(无改动{('：' + ref) if ref else ''})"
        body = diff[:6000] + ("\n…(diff 已截断，太长)" if len(diff) > 6000 else "")
        return f"{(stat.stdout or '').strip()}\n\n{body}"

    return [
        Tool("read_file",
             "读取仓库内某文件；给 start(/end) 读指定行段（1-based，含端点）——接 find_definition/"
             "document_symbols 给的行号跳到大文件深处；不给则整文件（超长截断、提示用行段）",
             {"path": "相对路径", "start": "可选，起始行号", "end": "可选，结束行号"},
             _read_file, read_only=True),
        Tool("list_files", "列出全仓库文本文件（py/js/html/md/yaml/toml… 跳过 .git/node_modules 等；"
             "可按子目录前缀过滤）", {"dir": "可选子目录"}, _list_files, read_only=True),
        Tool("glob", "按文件名模式找文件（不限文本扩展名、跳 .git/node_modules 等、最近修改排前）："
             "pattern 不含 / 匹配文件名（如 *.ts、*.test.js、conftest.py，任意深度），含 / 匹配相对路径"
             "（**最常用就给 *.ext）。可选 dir 限子目录",
             {"pattern": "文件名 glob，如 *.ts / **/*.py / src/**/*.css", "dir": "可选子目录"},
             _glob, read_only=True),
        Tool("grep", "在全仓库文本文件里按正则搜索（不止 src，含 web/examples/docs/配置等），"
             "返回 path:line 命中行；给 context=N 则带每处命中前后各 N 行（≤5，> 标命中行，"
             "类似 ripgrep -C，定位后不必再 read_file）",
             {"pattern": "正则", "dir": "可选子目录", "context": "可选，命中行前后各显示的行数(±N，≤5)"},
             _grep, read_only=True),
        Tool("analyze_repo", "只读扫描本仓库列出问题清单，无需 key", {}, _analyze_repo, read_only=True),
        Tool("find_definition",
             "语义查符号定义（jedi/LSP 级，跟随 import、比 grep 准）：给函数/类/变量名"
             "（可点号如 Class.method），返回定义位置+签名+文档",
             {"symbol": "符号名"}, _find_definition, read_only=True),
        Tool("find_references",
             "语义查符号的全项目引用（jedi/LSP 级）：给符号名，返回所有使用处 path:line",
             {"symbol": "符号名"}, _find_references, read_only=True),
        Tool("document_symbols",
             "列一个 .py 文件的类/函数结构大纲（jedi）：给路径，返回各定义的行号+签名，"
             "不必读全文就掌握其 API 面",
             {"path": "相对路径"}, _document_symbols, read_only=True),
        Tool("git_status", "看工作区 git 状态（git status -sb：当前分支 + 改动文件），只读",
             {}, _git_status, read_only=True),
        Tool("list_branches",
             "列本地分支（按最近提交排序、标当前分支）；尤其用来看 dev_isolated/dev_parallel/"
             "dev_auto 产出的 vorto/* 分支有哪些、各自最后提交。只读",
             {}, _list_branches, read_only=True),
        Tool("show_diff",
             "看 git diff（只读）：不给 ref 看工作区改动；给 ref 看指定范围，如 "
             "`main...vorto/x`（review dev_isolated/dev_parallel 落的分支，不必 checkout）",
             {"ref": "可选，git ref/范围，如 main...vorto/x"}, _show_diff, read_only=True),
    ]


def build_write_tools(root: str) -> list[Tool]:
    """构建写工具（edit_file/write_file），根目录限定在 root 且**无模态确认**——

    专给隔离 worktree 里的可写子 agent 用：改动只落在一次性工作树、最后整体出 diff 待人工确认，
    所以这里不逐条弹窗。带 `..` 越界防护，绝不写出 root 之外。
    """
    from pathlib import Path
    base = Path(root).resolve()

    def _safe(rel) -> Optional[Path]:
        return _resolve_within(base, rel)          # 与读工具共用同一边界防护，避免读/写漂移

    async def _edit_file(args: dict) -> str:
        rel = str(args.get("path", "")).strip()
        old, new = str(args.get("old", "")), str(args.get("new", ""))
        all_ = _truthy(args.get("replace_all", args.get("all")))
        p = _safe(rel)
        if p is None:
            return f"路径越界或非法: {rel}"
        if not old:
            return "edit_file 需要 old（要替换的原文）。"
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
        p.write_text(text.replace(old, new, n), encoding="utf-8")
        return f"已修改 {rel}（替换 {n} 处）。"

    async def _write_file(args: dict) -> str:
        rel = str(args.get("path", "")).strip()
        content = str(args.get("content", ""))
        p = _safe(rel)
        if p is None or p.is_dir():
            return f"路径越界或非法: {rel}"
        verb = "覆盖" if p.is_file() else "新建"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已{verb} {rel}（{len(content)} 字符）。"

    return [
        Tool("edit_file", "精确字符串替换：默认 old 须唯一存在（替 1 处）；old 出现多次时传 "
             "replace_all=true 一次替换全部（批量改名/统一字面量省去逐处加上下文）。在隔离工作区改文件",
             {"path": "相对路径", "old": "要替换的原文", "new": "替换为",
              "replace_all": "可选，true=替换全部出现处（默认仅在唯一时替 1 处）"},
             _edit_file, read_only=False),
        Tool("write_file", "新建或覆盖文件；在隔离工作区改文件",
             {"path": "相对路径", "content": "文件全部内容"}, _write_file, read_only=False),
    ]


def build_test_tool(root: str, default_cmd: Optional[list] = None) -> "Tool":
    """给隔离实现子 agent 一个**受限**的 run_tests 工具：只能在 root 跑测试（不是任意 shell），

    让它 implement→test→fix 自我迭代——产出的 diff 是"已经自己跑通的"，而不是盲改后才发现没过。
    命令按仓库类型自动探测（pytest/npm/go/cargo/make），不再写死 pytest；autonomous 也只跑测试、不乱执行。
    """
    async def _handler(args: dict) -> str:
        import asyncio
        import sys
        from src.agents.test_detect import detect_test_cmd, is_pytest_cmd
        from src.agents.worktree import run_tests
        base = default_cmd or detect_test_cmd(root)
        sel = str(args.get("test") or "").strip()
        # selector 只对 pytest 有意义（文件级 narrow）；非 pytest 命令忽略 selector、跑整套
        if sel and is_pytest_cmd(base):
            cmd = [sys.executable, "-m", "pytest", "-q", sel]
        else:
            cmd = list(base)
        res = await asyncio.to_thread(run_tests, root, cmd)
        tag = "通过 ✓" if res["ok"] else "未过 ✗"
        return f"测试{tag}（{res['cmd']}）。输出尾部：\n{res['output'][-2500:]}"

    return Tool("run_tests",
                "在当前隔离工作区跑测试自测（命令按仓库类型自动探测；pytest 可传 test 选择器 narrow，"
                "省略/非 pytest 跑整套）；实现后务必自测，没过就改完再测，直到通过",
                {"test": "可选，pytest 文件级选择器，如 tests/unit/test_x.py（仅 pytest 生效）"},
                _handler, read_only=True)


def _detect_base_branch(repo_root: str) -> str:
    """dev_auto 开 PR 时的 base：取当前 HEAD 所在分支（PR 合回你出发的地方）；分离头/出错回退 main。"""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True)
        b = (r.stdout or "").strip()
        return b if b and b != "HEAD" else "main"
    except Exception:  # noqa: BLE001
        return "main"


def _is_test_path(path: str) -> bool:
    """路径看着像测试文件（tests/、test_*.py、*_test.py、*.test./*.spec.）。"""
    p = (path or "").strip().lower()
    base = p.rsplit("/", 1)[-1]
    return ("tests/" in p or p.startswith("test/") or "/test/" in p
            or base.startswith("test_") or base.endswith("_test.py")
            or ".test." in base or ".spec." in base)


def _count_test_files(diff: str) -> int:
    """数一个 unified diff 里改动的**测试文件**数（从 `+++ b/<path>` 行取）。"""
    import re as _re
    return sum(1 for p in _re.findall(r"^\+\+\+ b/(.+)$", diff or "", _re.M) if _is_test_path(p))


def _branch_changed_files(repo_root: str, base: str, branch: str) -> Optional[list[str]]:
    """branch 相对 base 的改动文件路径（三点差：branch 分出后的全部改动）。

    dev_auto 用它算测试增量——**依赖接力**子任务经 run_dependent_on_branch 直接 commit 到分支，
    其 diff 不在内存 greens 里，只有分支上才看得全（否则纯依赖成功时会漏掉诚实提示）。出错返回 None。
    """
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(repo_root), "diff", "--name-only", f"{base}...{branch}"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return None
        return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    except Exception:  # noqa: BLE001
        return None


def _test_delta_msg(n_tests: int) -> str:
    """给定"本次改动的测试文件数"，生成诚实的测试增量说明。

    dogfood（2026-07-02）暴露：隔离实现子 agent 只加了源码、没加要求的测试，但 run_tests 里
    **既有测试仍绿** → 报"测试通过" → 主 agent 据此**谎称补了测试**。根因是"测试通过"这个信号
    不区分"既有测试还绿"与"新代码被覆盖"。这里如实点出测试文件增量，既给用户诚实信号，也给主
    agent 据实依据（别再编造补了测试）。
    """
    if n_tests:
        return f"（本次改动含 {n_tests} 个测试文件）"
    return ("（⚠ 本次改动**未新增/改动任何测试文件**——“测试通过”仅表示既有测试仍绿，"
            "新增/改动的代码未必被测试覆盖；若任务要求测试请核对是否真的补了）")


def _test_delta_note(diff: str) -> str:
    """按 unified diff 生成诚实的测试增量说明（dev_isolated/dev_parallel 用；它们的 diff 在内存里齐全）。"""
    return _test_delta_msg(_count_test_files(diff))


def build_dev_tools(repo_root: str, on_progress: Optional[Callable[[str], None]] = None,
                    confirm: Optional[Callable] = None) -> list[Tool]:
    """UI 无关的隔离 dev 工具（给 Web/CLI agent 用）。

    `dev_isolated`：在一次性 git worktree 里让可写子 agent 实现 + 自测，再跑测试验证；✅通过就
    **自动落到 vorto/<id> 新分支**（绝不碰 main/工作区），返回结论。无模态确认——靠 build 门控 +
    完全隔离 + 落新分支保证"人在关口"。TUI 那版另带富 diff 渲染 + 确认；这版给没有模态的 Web。

    on_progress(msg)：可选进度回调。dev_parallel/dev_auto 跑大任务时一个子任务就可能要 1-2 分钟、
    整条链路十几分钟——没有它调用方只能对着静默的 prompt 干等、像卡死。有了它能边跑边播
    "并行实现中/某块修复重试/依赖接力/集成验证…"。best-effort、出错不影响流水线；不传则零开销。

    confirm(message)->awaitable bool：可选**外向操作确认门**（同 build_command_tool）。给了它，
    dev_auto 才支持 `open_pr=true`——集成测试通过后经确认把 vorto/auto 分支 push 上去并开 PR，
    补上"一句话→PR"的最后一环。不传/未确认/集成红 都不开 PR（只留分支），完全向后兼容。
    """
    def _progress(msg: str) -> None:
        if on_progress is not None:
            try:
                on_progress(msg)
            except Exception:  # noqa: BLE001
                pass

    async def _dev_isolated(args: dict) -> str:
        import asyncio
        import re
        import uuid
        from src.agents.worktree import apply_diff_to_branch

        desc = str(args.get("description") or args.get("task") or "").strip()
        if not desc:
            return "dev_isolated 需要 description（要在隔离工作区实现的子任务）。"
        sel = str(args.get("test") or "").strip()
        from src.agents.test_detect import detect_test_cmd
        test_cmd = detect_test_cmd(repo_root, sel)          # 按仓库类型探测（pytest/npm/go/cargo/make）
        _progress(f"⚙️ 隔离实现「{desc[:40]}」中（worktree 实现+自测，可能要 1-2 分钟）…")
        try:
            # 复用自修复重试循环：子 agent no-op（没真改文件）或自测红时，换全新 worktree 自动重试，
            # 最多 _dev_attempts() 次。这样单次 dev_isolated 调用就不易交白卷，不必指望主 agent 再调一次。
            last = await _implement_with_repair(desc, test_cmd)
        except Exception as e:  # noqa: BLE001
            return f"(隔离实现出错: {e})"
        diff = (last.get("diff") or "")
        ver = last.get("ver")
        attempts = last.get("attempts", 1)
        fixed = f"（自修复 {attempts - 1} 次后）" if attempts > 1 else ""
        if not diff.strip():
            return (f"❌ 隔离实现未产生任何改动（试了 {attempts} 次，子 agent 始终没真正修改文件）。"
                    f"请把任务描述写得更具体、可执行（明确要改哪个文件、加什么）后再调 dev_isolated。")
        nlines = diff.count("\n")
        if ver and ver["ok"]:
            slug = re.sub(r"[^a-z0-9]+", "-", desc.lower()).strip("-")[:28] or "iso"
            branch = f"vorto/{slug}-{uuid.uuid4().hex[:8]}"
            res = await asyncio.to_thread(apply_diff_to_branch, repo_root, branch, diff, f"dev_isolated: {desc}")
            if res["ok"]:
                return (f"✅ 已隔离实现且测试通过{fixed}，落到新分支 {branch}（{nlines} 行，"
                        f"git checkout {branch} 查看，未碰 main）。" + _test_delta_note(diff))
            return f"✅ 实现且测试通过{fixed}，但落分支失败：{res['error']}。diff {nlines} 行。"
        tail = (ver or {}).get("output", "")[-1000:]
        return (f"❌ 隔离实现完成但测试未过（试了 {attempts} 次）。失败输出尾部：\n{tail}\n"
                f"据此修正后重试（再调 dev_isolated）。diff {nlines} 行，未落地。")

    def _make_writer(test_cmd):
        """造一个'隔离实现子 agent'工厂：worktree 里 read+write+run_tests、自测到通过再交。"""
        def _mk(_desc):
            def _b(wt):
                return MainAgent(
                    build_read_tools(wt) + build_write_tools(wt) + [build_test_tool(wt, test_cmd)],
                    max_steps=16,
                    extra_system=("你是隔离工作区里的实现子 agent：用 read/grep 看代码，然后**必须用 "
                                  "edit_file/write_file 实际修改文件**实现任务——只查看或只跑测试不改文件不算完成。"
                                  "改完务必 run_tests 自测直到通过。只动相关文件。"))
            return _b
        return _mk

    async def _implement_with_repair(desc, test_cmd):
        """隔离实现 desc + 自测；红了把失败输出拼回描述、换**全新 worktree** 再试，最多 _dev_attempts() 次。

        返回 {desc, diff, ver, attempts}。绿（ver.ok 且有 diff）即提前收口；都没绿则返回最后一次。
        每次都是干净 worktree + 全新子 agent（不背着上次的半成品），只把失败输出当线索喂进去。
        """
        import uuid
        from src.agents.worktree import run_isolated_task
        mk = _make_writer(test_cmd)
        cur = desc
        last = {"desc": desc, "diff": "", "ver": None, "attempts": 0}
        for attempt in range(1, _dev_attempts() + 1):
            if attempt > 1:
                _progress(f"↻ 「{desc[:32]}」上次未达标（红/无改动），第 {attempt} 次换全新 worktree 重试…")
            wid = "wt-" + uuid.uuid4().hex[:8]
            try:
                diff, _c, ver = await run_isolated_task(repo_root, wid, cur, mk(cur), test_cmd=test_cmd)
            except Exception as e:  # noqa: BLE001
                last = {"desc": desc, "diff": "", "ver": None, "attempts": attempt, "err": str(e)}
                continue
            last = {"desc": desc, "diff": diff, "ver": ver, "attempts": attempt}
            if ver and ver["ok"] and (diff or "").strip():
                _progress(f"✅ 「{desc[:32]}」实现并自测通过" + (f"（修复 {attempt - 1} 次后）" if attempt > 1 else ""))
                return last                                  # 绿了就收
            # 准备下一次的反馈：no-op（没改文件）和测试红是两码事，提示也不同
            if not (diff or "").strip():
                cur = _noop_retry_prompt(desc)               # 没改动：用更命令式提示逼它真动手
            else:
                cur = _repair_prompt(desc, (ver or {}).get("output", "")[-1500:])   # 红：带失败反馈再试
        _progress(f"❌ 「{desc[:32]}」试了 {_dev_attempts()} 次仍未过")
        return last

    async def _implement_parallel(descs, test_cmd):
        """并行隔离实现 descs（每个内部自修复重试），返回 (greens, lines)。lines=逐条 ✅/❌（标修复次数）。

        并发受 _dev_parallelism() 上限约束（信号量）：descs 很多时也只同时跑 N 个、其余排队，
        防一次性 fan-out 几十个 worktree+LLM 打爆中转站/磁盘——所有子任务仍都会被处理，只是不再挤在一起。
        """
        import asyncio
        cap = _dev_parallelism()
        _progress(f"⚙️ 并行隔离实现 {len(descs)} 个子任务中"
                  f"（各自起 worktree 实现+自测；最多 {cap} 个同时跑）…")
        sem = asyncio.Semaphore(cap)

        async def _bounded(t):
            async with sem:
                return await _implement_with_repair(t, test_cmd)

        results = await asyncio.gather(*[_bounded(t) for t in descs])
        lines, greens = [], []
        for r in results:
            att = r.get("attempts", 1)
            green = r.get("ver") and r["ver"]["ok"] and (r.get("diff") or "").strip()
            if green:
                lines.append(f"· {r['desc']}：✅ 通过" + (f"（修复 {att - 1} 次后）" if att > 1 else ""))
                greens.append(r)
            elif not (r.get("diff") or "").strip():
                lines.append(f"· {r['desc']}：无改动/出错" + (f"（试了 {att} 次）" if att > 1 else ""))
            else:
                lines.append(f"· {r['desc']}：❌ 未过（试了 {att} 次）")
        return greens, lines

    async def _dependent_with_repair(branch, subtask, test_cmd):
        """在 branch 之上接力实现一个依赖子任务 + 自测；红了带失败反馈、换全新 worktree 再试，最多 _dev_attempts() 次。
        绿则就地提交（推进 branch）后返回。返回 run_dependent_on_branch 的结果 dict（附 attempts）。"""
        import uuid
        from src.agents.decompose import describe_subtask
        from src.agents.worktree import run_dependent_on_branch
        mk = _make_writer(test_cmd)
        base = describe_subtask(subtask)
        cur = base
        title = getattr(subtask, "title", "") or getattr(subtask, "id", "?")
        msg = f"dev_auto(dep): {title}"
        r = {"ok": False, "output": "未尝试", "attempts": 0}
        for attempt in range(1, _dev_attempts() + 1):
            _progress(f"🔗 依赖接力实现「{title}」" + (f"（第 {attempt} 次修复重试）" if attempt > 1 else "…"))
            wid = "wt-" + uuid.uuid4().hex[:8]
            r = await run_dependent_on_branch(repo_root, wid, branch, cur, mk(None), msg, test_cmd)
            r["attempts"] = attempt
            if r["ok"]:
                return r
            cur = _repair_prompt(base, (r.get("output") or "")[-1500:])
        return r

    async def _dev_parallel(args: dict) -> str:
        import asyncio
        import uuid
        from src.agents.worktree import apply_diffs_to_branch

        tasks = args.get("tasks") or args.get("descriptions") or []
        if isinstance(tasks, str):
            tasks = [tasks]
        tasks = [str(t).strip() for t in tasks if str(t).strip()][:5]   # 最多 5，防失控
        if not tasks:
            return "dev_parallel 需要 tasks（相互独立的子任务字符串列表）。"
        sel = str(args.get("test") or "").strip()
        from src.agents.test_detect import detect_test_cmd
        test_cmd = detect_test_cmd(repo_root, sel)          # 按仓库类型探测（pytest/npm/go/cargo/make）

        greens, lines = await _implement_parallel(tasks, test_cmd)
        if not greens:
            return f"并行 {len(tasks)} 个子任务：无通过测试的改动。\n" + "\n".join(lines)
        branch = "vorto/parallel-" + uuid.uuid4().hex[:8]
        _progress(f"📦 {len(greens)} 块通过 → 落分支 {branch} 并跑集成测试中…")
        res = await asyncio.to_thread(
            apply_diffs_to_branch, repo_root, branch,
            [(g["diff"], f"dev_parallel: {g['desc']}") for g in greens],
            test_cmd)                                    # 落完在集成分支上再跑一遍全量，抓"单独绿合起来红"
        head = f"并行 {len(tasks)} 个子任务：{len(greens)} 通过测试。"
        if not res["applied"]:
            return head + "落分支失败。\n" + "\n".join(lines)
        integ = res.get("integration")
        if integ and not integ["ok"]:                    # 各块单独绿、但合到一起红 → 如实说，别谎报全绿
            tail = integ["output"][-1200:]
            note = (f"⚠️ {len(res['applied'])} 块已落到 {branch}，但**集成后全量测试未过**"
                    f"（单独绿、合起来红，多为语义冲突/相互破坏）。失败尾部：\n{tail}\n"
                    f"分支已保留待修：git checkout {branch}，据失败修正后再集成。")
        elif integ and integ["ok"]:
            note = (f"✅ {len(res['applied'])} 块落到 {branch} 且**集成后全量测试通过**（git checkout 查看，未碰 main）。"
                    + _test_delta_note("\n".join(g["diff"] for g in greens)))
        else:                                            # 没跑集成测试（理论上 test_cmd 恒有，留兜底）
            note = f"{len(res['applied'])} 块落到 {branch}（git checkout 查看，未碰 main）。"
        return head + note + "\n" + "\n".join(lines)

    async def _open_pr_for_branch(branch: str, task: str, body: str, base: str) -> str:
        """dev_auto 集成绿后、经确认把分支 push 并开 PR。confirm 缺失/被拒/失败都给清楚说明、不抛。"""
        import asyncio
        if confirm is None:
            return ("\n（本环境未接确认门，未自动开 PR；分支已就绪，可用 open_pr 工具手动开。）")
        title = f"dev_auto: {task[:60]}"
        if not await confirm(f"把 {branch} push 到远端并对 {base} 开 PR？\n  标题：{title}"):
            return f"\n（已取消开 PR；分支 {branch} 保留，可稍后手动 open_pr。）"
        _progress(f"🚀 push {branch} 并对 {base} 开 PR…")
        from src.agents.vcs import push_and_open_pr
        res = await asyncio.to_thread(push_and_open_pr, repo_root, branch, title, body[:4000], base)
        if res.get("ok") and res.get("url"):
            return f"\n🎉 已开 PR：{res['url']}"
        if res.get("pushed"):
            return f"\n（已 push {branch}，但开 PR 失败：{res.get('error')}。可手动 gh pr create。）"
        return f"\n（开 PR 失败：{res.get('error')}；分支 {branch} 保留。）"

    async def _dev_auto(args: dict) -> str:
        """自动分解大任务 → 无依赖子任务并行隔离实现 → **有依赖的按拓扑序在同一分支上逐个接力实现**
        （检出该分支、看得见前面的改动、自测绿才提交、推进 tip 给下一个看）→ 最后对整条分支跑一遍
        集成测试。端到端把大任务做完，不再只做独立那一半就停。全程不碰 main/工作区。
        给 open_pr=true 且接了确认门：集成绿后经确认把分支 push 并开 PR（"一句话→PR"闭环）。"""
        import asyncio
        import uuid
        from src.agents.decompose import decompose_for_parallel, topo_order
        from src.agents.worktree import apply_diffs_to_branch, ensure_branch, verify_branch

        task = str(args.get("task") or args.get("goal") or args.get("description") or "").strip()
        if not task:
            return "dev_auto 需要 task（要自动分解并实现的大任务）。"
        want_pr = _truthy(args.get("open_pr") or args.get("pr") or False)
        base = _detect_base_branch(repo_root)               # PR base：dev_auto 出发时所在分支
        sel = str(args.get("test") or "").strip()
        from src.agents.test_detect import detect_test_cmd
        test_cmd = detect_test_cmd(repo_root, sel)          # 按仓库类型探测（pytest/npm/go/cargo/make）
        _progress("🧩 自动分解任务中…")
        try:
            plan = await decompose_for_parallel(task)
        except Exception as e:  # noqa: BLE001
            return f"(任务分解出错: {e}；可改用 dev_parallel 手动给独立子任务)"
        descs, deferred = plan["descriptions"], plan["deferred"]
        if not descs and not deferred:
            return f"分解出 {plan['total']} 个子任务，但没拿到可实现的描述；建议用 dev_isolated 逐个做。"

        branch = "vorto/auto-" + uuid.uuid4().hex[:8]
        out = [f"已把任务分解为 {plan['total']} 个子任务：{len(descs)} 个独立(并行) + {len(deferred)} 个有依赖(接力)。"]

        # 1) 独立子任务并行隔离实现 → 落到 branch（此处不跑集成，留到最后整条一起验）
        greens, lines = (await _implement_parallel(descs, test_cmd)) if descs else ([], [])
        if greens:
            await asyncio.to_thread(
                apply_diffs_to_branch, repo_root, branch,
                [(g["diff"], f"dev_auto: {g['desc']}") for g in greens], None)
        elif deferred:
            await asyncio.to_thread(ensure_branch, repo_root, branch, "HEAD")  # 无绿独立块也给依赖一个基底
        if descs:
            out.append(f"\n【独立批】{len(greens)}/{len(descs)} 通过：")
            out.extend("  " + ln for ln in lines)

        # 2) 依赖子任务：拓扑序，逐个在 branch 之上接力实现+自测（红了自修复重试），绿则就地提交（推进 branch）
        dep_done = 0
        if deferred:
            out.append("\n【依赖接力】按拓扑序在分支上逐个实现：")
            satisfied = set(getattr(s, "id", None) for s in plan["independent"])  # 独立批视为已满足(best-effort)
            for s in topo_order(deferred, satisfied):
                r = await _dependent_with_repair(branch, s, test_cmd)
                title = getattr(s, "title", "") or getattr(s, "id", "?")
                att = r.get("attempts", 1)
                if r["ok"]:
                    dep_done += 1
                    out.append(f"  · {title}：✅ 已接力提交" + (f"（修复 {att - 1} 次后）" if att > 1 else ""))
                else:
                    out.append(f"  · {title}：❌ 试了 {att} 次仍未过：{(r['output'] or '')[-140:]}")

        # 3) 最终集成验证：整条分支跑一遍全量
        if not greens and dep_done == 0:
            return "\n".join(out) + "\n\n没有任何子任务落地（都没过自测）；建议拆细或用 dev_isolated 逐个做。"
        _progress(f"🔍 对整条分支 {branch}（{len(greens)} 独立 + {dep_done} 依赖）跑最终集成测试中…")
        integ = await asyncio.to_thread(
            verify_branch, repo_root, branch, test_cmd, "wt-verify-" + uuid.uuid4().hex[:8])
        if integ["ok"]:
            done = (f"\n✅ 全部落到 {branch}（{len(greens)} 独立 + {dep_done} 依赖）且**集成后全量测试通过**"
                    f"（未碰 main，git checkout {branch} 查看）。")
            # 诚实提示测试增量：从**整条分支相对 base 的实际 diff**算——独立批 + 依赖接力提交都覆盖到
            # （依赖接力的改动不在内存 greens 里，只看 greens 会让纯依赖成功时漏提示，见 codex 审）。
            changed = await asyncio.to_thread(_branch_changed_files, repo_root, base, branch)
            if changed is not None:
                done += _test_delta_msg(sum(1 for p in changed if _is_test_path(p)))
            out.append(done)
            if want_pr:                                      # 集成绿 + 要求开 PR → 经确认 push+开 PR
                out.append(await _open_pr_for_branch(branch, task, "\n".join(out), base))
        else:
            out.append(f"\n⚠️ 已落到 {branch}（{len(greens)} 独立 + {dep_done} 依赖），但**集成后全量测试未过**。"
                       f"失败尾部：\n{integ['output'][-1000:]}\n分支保留待修：git checkout {branch}。")
            if want_pr:
                out.append("（集成测试未过，未自动开 PR——先把分支修绿再开。）")
        return "\n".join(out)

    return [
        Tool("dev_isolated",
             "在隔离 git worktree 里实现一个独立子任务 + 自测 + 跑测试验证；✅通过就自动落到一个"
             "vorto/<id> 新分支（绝不碰 main/工作区），❌带失败输出供修正。仅 build",
             {"description": "要在隔离工作区实现的子任务",
              "test": "可选，pytest 选择器，省略则跑全量 tests/"},
             _dev_isolated, read_only=False),
        Tool("dev_parallel",
             "并行实现：多个**相互独立**的子任务各起隔离 worktree 同时实现+自测+验证（互不冲突，"
             "红了带失败反馈自修复重试），绿块一并落到一个 vorto/parallel 新分支（不碰 main），"
             "**落分支后再跑一遍集成测试**抓'单独绿合起来红'，汇报各自 ✅/❌ 及集成结果。最多 5（仅 build）",
             {"tasks": "相互独立的子任务字符串列表",
              "test": "可选，pytest 选择器，省略则各自跑全量 tests/"},
             _dev_parallel, read_only=False),
        Tool("dev_auto",
             "把一个大任务**端到端**做完：自动分解→无依赖子任务并行隔离实现→**有依赖的按拓扑序"
             "在同一 vorto/auto 分支上逐个接力实现**（看得见前面的改动、自测绿才提交）→最后整条分支"
             "跑一遍集成测试。子任务红了都会带失败反馈自修复重试。不再只做独立那一半就停。绝不碰 main。"
             "给 open_pr=true 则集成通过后（经确认）把分支 push 并开 PR，一句话直达 PR。仅 build",
             {"task": "要自动分解并实现的大任务（自然语言）",
              "test": "可选，pytest 选择器",
              "open_pr": "可选，true 则集成绿后经确认 push 分支并开 PR"},
             _dev_auto, read_only=False),
    ]


def build_research_tools(repo_root: str, *, llm: Any = None,
                         max_steps: int = 12, max_parallel: int = 5) -> list[Tool]:
    """UI 无关的只读子 agent 委派工具（task / research_parallel）——给 Web/CLI 用。

    把一个大型只读调查甩给一个**只带 read_tools** 的隔离子 agent：它在独立上下文里
    读代码/搜仓库、返回简洁结论，**不挤占也不污染主 agent 的对话历史**（大调查不再把
    主上下文撑爆——配合对话压缩，是"扛大工程量"的另一条腿）。子 agent 无 task/写工具
    → 不会递归嵌套、绝不改文件。TUI 另有带进度回显的版本（self._chrome），此处是无 UI 版。
    llm 可注入（便于测试/共享客户端）；不传则子 agent 各自惰性建客户端（同 TUI）。
    """
    async def _spawn(desc: str) -> str:
        # 子 agent 只读：mode 用 plan（read_tools 里无写工具，权限门对它无差别）。
        sub = MainAgent(build_read_tools(repo_root), llm=llm, max_steps=max_steps, extra_system=(
            "你是只读研究子 agent：只用工具调研代码/仓库并返回**简洁结论**，绝不修改任何东西。"
            "读够信息就尽快收口，别把预算耗在重复读取上。"))
        try:
            return (await sub.run_turn(desc, mode="plan")) or "(无结论)"
        except Exception as e:  # noqa: BLE001
            return f"(子任务出错: {e})"

    async def _task(args: dict) -> str:
        desc = str(args.get("description") or args.get("task") or "").strip()
        if not desc:
            return "task 需要 description（要委派给只读子 agent 的研究/调研子任务）。"
        return await _spawn(desc)

    async def _research_parallel(args: dict) -> str:
        import asyncio
        tasks = args.get("tasks") or args.get("descriptions") or []
        if isinstance(tasks, str):
            tasks = [tasks]
        tasks = [str(t).strip() for t in tasks if str(t).strip()][:max_parallel]
        if not tasks:
            return "research_parallel 需要 tasks（字符串列表，每项一个独立子问题）。"
        results = await asyncio.gather(*[_spawn(t) for t in tasks])
        return "\n\n".join(f"【{t}】\n{r}" for t, r in zip(tasks, results))

    return [
        Tool("task",
             "把一个独立的研究/调研子任务委派给只读子 agent（隔离上下文、不污染主对话），返回它的结论；"
             "适合大型只读调查（读一堆文件/摸清某子系统）——别在主对话里逐个读，委派出去省上下文",
             {"description": "要委派给子 agent 的研究/调研子任务"}, _task, read_only=True),
        Tool("research_parallel",
             "并行委派多个只读子 agent 同时研究不同**相互独立**的子问题，汇总各自结论（最多 5 个）",
             {"tasks": "独立子问题字符串列表"}, _research_parallel, read_only=True),
    ]


def build_web_tools() -> list[Tool]:
    """联网工具：`web_fetch`（按 URL 抓正文）+ `web_search`（按查询找网页）。

    两者都只读但外向。web_fetch 抓公网 http(s) URL 正文（SSRF 防护/封顶/超时/HTML→正文，
    见 src/agents/web_fetch.py）。web_search 走 DuckDuckGo HTML 端点把查询变成结果列表
    （无需 API key，见 src/agents/web_search.py），典型用法：web_search 找链接 → web_fetch 深读。
    read_only=True → plan 也可用、无需逐条确认（GET 不改状态，风险靠 SSRF/封顶/超时挡）。"""
    async def _web_fetch(args: dict) -> str:
        import asyncio
        from src.agents.web_fetch import fetch_url
        url = str(args.get("url") or args.get("href") or "").strip()
        return await asyncio.to_thread(fetch_url, url)

    async def _web_search(args: dict) -> str:
        import asyncio
        from src.agents.web_search import web_search
        query = str(args.get("query") or args.get("q") or "").strip()
        return await asyncio.to_thread(web_search, query)

    return [Tool("web_fetch",
                 "抓取一个公网 http(s) 网址的正文（查文档/issue/报错页/API 说明）：限 http/https、"
                 "拒私网与环回(SSRF 防护)、下载封顶、HTML 自动转正文。只读、无需确认",
                 {"url": "要抓取的 http(s) 网址"}, _web_fetch, read_only=True),
            Tool("web_search",
                 "联网搜索（DuckDuckGo，无需 key）：给查询返回若干「标题/URL/摘要」，再用 web_fetch "
                 "深读感兴趣的链接。查最新信息/报错/库用法时先搜后读。只读、无需确认",
                 {"query": "搜索关键词/问题"}, _web_search, read_only=True)]


def build_command_tool(repo_root: str, confirm) -> list[Tool]:
    """UI 无关的 run_command（给 Web 用，注入 async confirm 门）。

    高危但带三层关口：危险操作硬拒 + 逐条 `await confirm(msg)` 确认 + build 门控。
    confirm(message) 是 async、返回 bool（Web 端走 WS 确认；超时/拒绝都安全不跑）。
    """
    async def _run(args: dict) -> str:
        import asyncio
        from src.agents.shell import is_dangerous, run_command
        cmd = str(args.get("command") or args.get("cmd") or "").strip()
        if not cmd:
            return "run_command 需要 command。"
        why = is_dangerous(cmd)
        if why:
            return f"拒绝执行（疑似危险操作：{why}）。请换更具体、安全的命令。"
        if not await confirm(f"在仓库根目录执行命令？\n  $ {cmd}"):
            return f"用户拒绝了命令：{cmd}"
        res = await asyncio.to_thread(run_command, repo_root, cmd)
        return f"命令 `{cmd}` 退出码 {res['code']}。输出尾部：\n{res['output'][-3000:]}"

    return [Tool("run_command",
                 "在仓库根目录跑任意 shell 命令（pytest/ruff/git/pip/make…）；高危，每条都需确认、"
                 "明显危险操作直接拒（仅 build）",
                 {"command": "要执行的 shell 命令"}, _run, read_only=False)]


def build_pr_tool(repo_root: str, confirm) -> list[Tool]:
    """UI 无关的 open_pr（给 Web 用，注入 async confirm 门）。外向操作：push + gh pr create，需确认。"""
    async def _open_pr(args: dict) -> str:
        import asyncio
        from src.agents.vcs import push_and_open_pr
        branch = str(args.get("branch", "")).strip()
        title = str(args.get("title", "")).strip()
        body = str(args.get("body", "")).strip()
        if not branch or not title:
            return "open_pr 需要 branch 和 title。"
        if not await confirm(f"把分支 {branch} push 到 origin 并开 PR「{title}」？这是外向操作（推到远端、建 PR）。"):
            return f"用户拒绝了为 {branch} 开 PR。"
        res = await asyncio.to_thread(push_and_open_pr, repo_root, branch, title, body)
        if res["ok"]:
            return f"已 push {branch} 并开 PR：{res['url']}"
        if res.get("pushed"):
            return f"已 push {branch}，但开 PR 失败：{res['error']}（可手动 gh pr create）"
        return f"开 PR 失败：{res['error']}"

    return [Tool("open_pr",
                 "把一个本地分支（如 dev_isolated 产出的 vorto/...）push 到 origin 并开 PR；"
                 "外向操作、需确认，gh 不可用则只 push（仅 build）",
                 {"branch": "要开 PR 的分支名", "title": "PR 标题", "body": "可选，PR 正文"},
                 _open_pr, read_only=False)]


def build_agent_tools(repo_root: str, *, confirm, on_progress: Optional[Callable[[str], None]] = None,
                      with_artifacts: bool = False) -> list[Tool]:
    """标准主 agent 工具集（headless CLI 与 Web /agent 共用，保证二者"同源"、不漂移）。

    此前 cli._build_headless_agent 与 web._new_agent 各自手写同一串 build_*，极易漂移
    （工具清单/顺序/confirm 语义不一致）。收敛到这里一处装配：
      read（行段/grep/glob/git 只读/语义导航）+ research（只读子 agent 委派）+ web（fetch/search）
      [+ artifact（发布/列制品，仅 with_artifacts）] + dev（隔离实现/并行，绿落 vorto 分支）
      + command（run_command）+ pr（open_pr）。
    confirm: async (message)->bool 确认门——CLI 走 --yes 门控、Web 走 WS 确认，语义由调用方注入。
    on_progress: dev 流水线进度回调（长任务边跑边播）。
    with_artifacts: 是否含制品工具（Web 有查看页故开；headless CLI 无浏览器故关）。
    TUI 不走本工厂——它用富 UI 版写/dev/command 工具（着色 diff + ConfirmScreen），刻意不同源。
    """
    tools = build_read_tools(repo_root) + build_research_tools(repo_root) + build_web_tools()
    if with_artifacts:
        from src.web.artifacts import build_artifact_tools    # 惰性导入：避免 agents 层在导入期硬依赖 web
        tools += build_artifact_tools(repo_root)
    tools += (build_dev_tools(repo_root, on_progress=on_progress, confirm=confirm)
              + build_command_tool(repo_root, confirm) + build_pr_tool(repo_root, confirm))
    return tools
