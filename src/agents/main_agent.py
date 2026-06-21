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
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

# 单个工具结果回灌给模型的最大字符数，避免长输出把上下文撑爆
_MAX_TOOL_RESULT = 4000

# 工具预算用尽时的"收尾"指令：禁用工具、强制据已有上下文给最终回答（而不是丢弃一切返回空）
_FORCE_FINISH_RULE = (
    "\n\n【收尾】工具调用预算已用尽：现在**禁止再调用任何工具**，"
    "直接根据上文已获取的信息给出最终结论/回答；信息不全就基于现有内容尽力总结并点明欠缺，"
    "不要输出任何工具调用 JSON。")


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


SYSTEM_TEMPLATE = """你是 VortoCode 的主助手，在一个终端 TUI 里和用户对话。VortoCode 是一个多 Agent 软件开发框架。
你可以调用下列工具来读代码、扫描仓库，或把"正经的开发任务"交给开发流水线（dev→test→review，会真跑测试与审查）：

{catalog}

【工具调用协议】需要调用工具时，**只输出一个 JSON 对象**，不要任何多余文字：
{{"tool": "工具名", "args": {{参数...}}}}
不需要工具、要回答用户时，**直接输出自然语言**（中文、简洁），不要输出 JSON。
每一步最多调用一个工具；看到 [工具 X 结果] 后，再决定下一步或给出最终回答。

【当前模式】{mode}（{mode_desc}）。{mode_rule}

【原则】寒暄、提问、解释概念、读/找代码这类轻活，直接回答或用只读工具，**不要**动用开发流水线；
只有用户明确要"实现/编写/修改某个具体功能"时，才用 run_dev_workflow。你只产出/提议，绝不假装已合并代码。"""


class MainAgent:
    """主 agent loop。无 UI 依赖：通过 say/emit 回调输出，工具由外部注入。"""

    def __init__(
        self,
        tools: list[Tool],
        llm: Any = None,
        max_steps: int = 6,
        max_history: int = 24,
        extra_system: Optional[str] = None,
        native: bool = False,
        on_tool: Optional[Callable[[str, dict, str], None]] = None,
        on_escalate: Optional[Callable[[str, dict], Awaitable[bool]]] = None,
        on_plan: Optional[Callable[[list], None]] = None,
        plan_tool: bool = False,
    ) -> None:
        import os
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
        self.max_history = max_history
        self.extra_system = extra_system       # 追加到系统提示（如技能目录、子 agent 角色）
        self._native = native                  # 原生 function-calling（失败自动回退提示式协议）
        self._on_tool = on_tool                # 工具执行后的审计钩子(name, args, result)
        # plan 模式想用写/重型工具时回调：返回 True=用户同意切 build 并继续，False=拒绝
        self._on_escalate = on_escalate
        self._escalated = False                # 本轮是否已升级到 build（经 on_escalate 同意）
        self.history: list[dict] = []          # 跨轮对话历史（不含 system）

    def _client(self) -> Any:
        # 惰性构建并缓存：跨步/跨轮复用同一个客户端（复用底层连接池），也便于测试注入
        if self._llm is None:
            from src.llm.client import LLMClient
            self._llm = LLMClient()
        return self._llm

    def _system(self, mode: str) -> str:
        mode_desc = "只读/提案" if mode == "plan" else "可写分支"
        mode_rule = (
            "plan 模式下写/重型工具（如 run_dev_workflow）不可用；"
            "若用户想开发，请提示他按 Tab 切到 build 模式。"
            if mode == "plan"
            else "build 模式下所有工具可用。"
        )
        prompt = SYSTEM_TEMPLATE.format(
            catalog=_tool_catalog(self._tool_list),
            mode=mode, mode_desc=mode_desc, mode_rule=mode_rule,
        )
        hint = self._orchestration_hint()      # 据可用工具给"大任务怎么展开"的编排指引
        if hint:
            prompt += "\n\n" + hint
        if self.extra_system:
            prompt += "\n\n" + self.extra_system
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
        if not (has_plan or has_iso or has_par):
            return ""
        lines = ["【怎么干大活】面对多步骤/较大的开发任务，别一上来就埋头改文件，按这个来："]
        if has_plan:
            lines.append("1) 先用 update_plan 把任务拆成有序步骤、列计划；每开始一步标 in_progress、做完标 "
                         "completed，让进度始终可见。")
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

    def _trimmed_history(self) -> list[dict]:
        if len(self.history) <= self.max_history:
            return list(self.history)
        return self.history[-self.max_history:]

    async def _complete(self, messages: list[dict], stream_cb: Optional[Callable[[str], None]]) -> str:
        """取一步模型输出。

        给了 stream_cb 且客户端支持流式 → 边生成边回显；但**疑似工具调用**（首个非空字符是
        `{` 或 ``` ）则静默缓冲、不把原始 JSON 流给 UI。否则退回一次性 chat（也便于测试）。
        """
        client = self._client()
        if stream_cb is None or not hasattr(client, "stream"):
            resp = await client.chat(messages, temperature=0.3)
            return resp.get("content") or ""
        buf: list[str] = []
        show: Optional[bool] = None        # None=未定；True=显示；False=抑制(疑似工具调用)
        async for tok in client.stream(messages, temperature=0.3):
            buf.append(tok)
            if show is None:
                head = "".join(buf).lstrip()
                if head:
                    show = not (head.startswith("{") or head.startswith("```"))
            if show:
                stream_cb("".join(buf))
        return "".join(buf)

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
        say(f"🔧 [b]{name}[/b][dim] {_fmt_args(args)}[/dim]")
        try:
            result = str(await tool.handler(args))
        except Exception as e:  # noqa: BLE001
            result = f"工具 {name} 执行出错: {e}"
        if len(result) > _MAX_TOOL_RESULT:
            result = result[:_MAX_TOOL_RESULT] + "\n…(结果已截断)"
        if self._on_tool is not None:           # 审计钩子（失败不影响工具）
            try:
                self._on_tool(name, args, result)
            except Exception:  # noqa: BLE001
                pass
        return result

    async def run_turn(
        self,
        user_text: str,
        mode: str = "plan",
        say: Optional[Callable[[str], None]] = None,
        emit: Optional[Callable[[str], None]] = None,
        stream_cb: Optional[Callable[[str], None]] = None,
    ) -> str:
        """处理一轮用户输入：循环"模型↔工具"，最终把回复交给 emit；并返回最终回复文本。

        say(markup): UI 提示行（如"🔧 调用工具"）；emit(text): 最终/工具的字面输出。
        stream_cb(partial): 给了就把"最终回复"边生成边流式回显（疑似工具调用的 JSON 不显示）。
        返回值：最终回复文本（供子 agent 把结论交回父 agent）。
        """
        say = say or (lambda _m: None)
        emit = emit or (lambda _m: None)
        self._escalated = False                # 每轮重置；切 build 由 UI 持久化到 mode
        self.history.append({"role": "user", "content": user_text})

        for _step in range(self.max_steps):
            messages = [{"role": "system", "content": self._system(mode)}] + self._trimmed_history()

            # 原生 function-calling 路径（opt-in）；模型不支持就永久回退到提示式协议
            if self._native:
                try:
                    resp = await self._client().chat(
                        messages, temperature=0.3, tools=self._tools_schema())
                except Exception:  # noqa: BLE001
                    self._native = False
                    resp = None
                if resp is not None:
                    tcs = resp.get("tool_calls")
                    content = (resp.get("content") or "").strip()
                    if not tcs:                    # 最终回复
                        self.history.append({"role": "assistant", "content": content})
                        emit(content or "(无回复)")
                        return content
                    tc = tcs[0]                     # 保持"单步一个工具"语义，取第一个
                    name = tc.get("name", "")
                    try:
                        args = json.loads(tc.get("arguments") or "{}")
                    except Exception:  # noqa: BLE001
                        args = {}
                    if not isinstance(args, dict):
                        args = {}
                    # 用提示式历史表示这一步（简单稳健，跨协议一致）
                    self.history.append({"role": "assistant",
                                         "content": json.dumps({"tool": name, "args": args}, ensure_ascii=False)})
                    result = await self._run_tool(name, args, mode, say)
                    self.history.append({"role": "user", "content": f"[工具 {name} 结果]\n{result}"})
                    continue

            # 提示式协议（默认；也是 native 回退后的路径）
            try:
                content = (await self._complete(messages, stream_cb)).strip()
            except Exception as e:  # noqa: BLE001
                # 有些异常 str 为空（如 5xx），给类型+折行截断的 detail 才可诊断（如 502 Bad Gateway）
                detail = " ".join((str(e) or repr(e)).split())[:200]
                hint = "（多为中转站/网络/额度问题，稍后重试或检查 OPENAI_API_BASE/KEY）" \
                    if any(k in detail for k in ("502", "503", "504", "Bad Gateway",
                                                 "Connection", "Timeout", "timed out")) else ""
                emit(f"对话出错: {type(e).__name__}: {detail}{hint}")
                return ""
            call = parse_tool_call(content)
            if call is None:                       # 最终回复
                self.history.append({"role": "assistant", "content": content})
                emit(content or "(无回复)")
                return content
            name, args = call
            self.history.append({"role": "assistant", "content": content})
            result = await self._run_tool(name, args, mode, say)
            self.history.append({"role": "user", "content": f"[工具 {name} 结果]\n{result}"})

        # 用尽工具预算：不白跑——强制一次"无工具"收尾，把已收集的信息综合成最终回答
        # （子 agent 尤其受益：读了一堆文件也能交回结论，而不是返回空丢弃全部上下文）。
        return await self._force_finish(mode, stream_cb, emit)

    async def _force_finish(self, mode: str,
                            stream_cb: Optional[Callable[[str], None]],
                            emit: Callable[[str], None]) -> str:
        """工具预算用尽后的收尾：禁用工具、强制据已有上下文给最终回答，避免丢弃全部工作。"""
        messages = [{"role": "system", "content": self._system(mode) + _FORCE_FINISH_RULE}] \
            + self._trimmed_history()
        try:
            content = (await self._complete(messages, stream_cb)).strip()
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


def build_read_tools(repo_root: str) -> list[Tool]:
    """构建一组只读工具（read_file/list_files/grep/analyze_repo），UI 无关，供 TUI/Web 共用。"""
    from pathlib import Path

    def _files() -> list[str]:
        base = Path(repo_root)
        out: list[str] = []
        for sub in ("src", "tests"):
            d = base / sub
            if d.is_dir():
                out += [str(p.relative_to(base)) for p in sorted(d.rglob("*.py"))
                        if "__pycache__" not in p.parts]
        return out[:2000]

    async def _read_file(args: dict) -> str:
        rel = str(args.get("path", "")).strip().lstrip("@")
        if not rel:
            return "缺少 path 参数。"
        p = Path(repo_root) / rel
        try:
            return p.read_text(encoding="utf-8")[:6000] if p.is_file() else f"(不存在: {rel})"
        except Exception as e:  # noqa: BLE001
            return f"读取失败: {e}"

    async def _list_files(args: dict) -> str:
        sub = str(args.get("dir", "")).strip().strip("/")
        fs = [f for f in _files() if f.startswith(sub)] if sub else _files()
        return "\n".join(fs[:200]) if fs else "(无源码文件)"

    async def _grep(args: dict) -> str:
        pat = str(args.get("pattern", "")).strip()
        if not pat:
            return "缺少 pattern 参数。"
        try:
            rx = re.compile(pat)
        except re.error as e:
            return f"无效正则: {e}"
        base = Path(repo_root)
        hits: list[str] = []
        for f in _files():
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

    async def _analyze_repo(args: dict) -> str:
        from src.orchestrator.self_analysis import analyze_self, render_report
        return render_report(await analyze_self(repo_root))

    return [
        Tool("read_file", "读取仓库内某个文件的内容", {"path": "相对路径"}, _read_file, read_only=True),
        Tool("list_files", "列出仓库源码文件（可按子目录前缀过滤）", {"dir": "可选子目录"},
             _list_files, read_only=True),
        Tool("grep", "在仓库源码里按正则搜索，返回 path:line 命中行",
             {"pattern": "正则", "dir": "可选子目录"}, _grep, read_only=True),
        Tool("analyze_repo", "只读扫描本仓库列出问题清单，无需 key", {}, _analyze_repo, read_only=True),
    ]


def build_write_tools(root: str) -> list[Tool]:
    """构建写工具（edit_file/write_file），根目录限定在 root 且**无模态确认**——

    专给隔离 worktree 里的可写子 agent 用：改动只落在一次性工作树、最后整体出 diff 待人工确认，
    所以这里不逐条弹窗。带 `..` 越界防护，绝不写出 root 之外。
    """
    from pathlib import Path
    base = Path(root).resolve()

    def _safe(rel) -> Optional[Path]:
        rel = str(rel or "").strip().lstrip("@")
        if not rel:
            return None
        p = (base / rel).resolve()
        try:
            p.relative_to(base)                    # 越界(..)/绝对路径 → 拒绝
        except ValueError:
            return None
        return p

    async def _edit_file(args: dict) -> str:
        rel = str(args.get("path", "")).strip()
        old, new = str(args.get("old", "")), str(args.get("new", ""))
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
        if cnt > 1:
            return f"原文在 {rel} 中出现 {cnt} 次、不唯一；请给更长、唯一的 old。"
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"已修改 {rel}（替换 1 处）。"

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
        Tool("edit_file", "精确字符串替换（old 须唯一存在）；在隔离工作区改文件",
             {"path": "相对路径", "old": "要替换的原文(需唯一)", "new": "替换为"},
             _edit_file, read_only=False),
        Tool("write_file", "新建或覆盖文件；在隔离工作区改文件",
             {"path": "相对路径", "content": "文件全部内容"}, _write_file, read_only=False),
    ]


def build_test_tool(root: str, default_cmd: Optional[list] = None) -> "Tool":
    """给隔离实现子 agent 一个**受限**的 run_tests 工具：只能在 root 跑测试（不是任意 shell），

    让它 implement→test→fix 自我迭代——产出的 diff 是"已经自己跑通的"，而不是盲改后才发现没过。
    安全：只跑 pytest，autonomous 也不会乱执行命令。
    """
    async def _handler(args: dict) -> str:
        import asyncio
        import sys
        from src.agents.worktree import run_tests
        sel = str(args.get("test") or "").strip()
        cmd = ([sys.executable, "-m", "pytest", "-q", sel] if sel
               else (default_cmd or [sys.executable, "-m", "pytest", "-q"]))
        res = await asyncio.to_thread(run_tests, root, cmd)
        tag = "通过 ✓" if res["ok"] else "未过 ✗"
        return f"测试{tag}（{res['cmd']}）。输出尾部：\n{res['output'][-2500:]}"

    return Tool("run_tests",
                "在当前隔离工作区跑测试自测（可传 test 选择器 narrow，省略跑默认集）；"
                "实现后务必自测，没过就改完再测，直到通过",
                {"test": "可选，pytest 选择器，如 tests/unit/test_x.py"},
                _handler, read_only=True)
