"""流水线工序执行器——把一道工序接到真的带角色子 agent 上。

`gateway/pipeline.py` 只管"推到哪了、谁在等谁"，刻意不 import agent 层（与 review.py 让
main_agent 注入 reviewer 同一理由，避免反向依赖成环）。本模块住在 agents 侧，负责把
`StageDef + 输入产出物` 变成一次真回合，并在这里完成三处**必须由执行侧做**的接线：

## 一、产出物污点 → 回合级污点（本模块存在的首要理由）

`taint.py` 的污点是回合级的（ContextVar），`products.py` 的污点是跨天存在的。两者之间需要
一根线：**读进带污点的产出物时，本回合必须进入污点态**——否则一个三天前从 web_search 抓来的
选题池，今天被读进来写文章时，这个回合看起来干干净净，免确认授权照常有效。那正是提示注入 D0
的口子换了个时间维度重开。这根线接在这里，因为只有执行侧同时看得见"输入是什么"和"回合是谁"。

## 二、outbound 工序过确认门

发布是对外动作。`confirm is None`（无确认通道，如无人值守）→ **拒绝，fail-closed**，与
`_spawn` 对 dev 型角色的处置同一哲学。污点态下确认文案带上来源说明——但**只在真有污点时带**，
不常亮（taint.py 的既定教训：把狼来了喊成日常就是拆防线）。

## 三、产出解析：解析不出就是没做完

工序声明 `output: json` 就必须给出一个 JSON 对象；给不出即抛错、该工序判失败。**不静默退化成
纯文本**——那正是"审查解析不出就当没问题"的同款病，今天刚在 review.py 修过一轮。想要自由文本
的工序显式写 `output: text`。
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

from src.gateway.pipeline import StageDef
from src.gateway.products import Product

_MAX_INPUT_CHARS = 12000          # 单条输入产出物喂进提示词的上限（超了截断并明说截断过）
_OBJECT = re.compile(r"\{.*\}", re.S)


def _render_inputs(inputs: List[Product]) -> str:
    """把输入产出物渲染成提示词里的一段。**明确标注哪些是外部来源**。"""
    if not inputs:
        return "（本工序没有上游产出物——这是本轮的第一道工序，或上游还没产出。）"
    blocks = []
    for product in inputs:
        body = json.dumps(product.payload, ensure_ascii=False, indent=2)
        truncated = len(body) > _MAX_INPUT_CHARS
        if truncated:
            body = body[:_MAX_INPUT_CHARS] + "\n…（内容过长已截断）"
        mark = "（⚠ 含外部摄入内容）" if product.tainted else ""
        blocks.append(f"--- {product.kind} · {product.id}{mark} ---\n"
                      f"{product.summary}\n{body}")
    return "\n\n".join(blocks)


def _parse_output(reply: str, stage_def: StageDef) -> Dict[str, Any]:
    """按工序声明的口径解析产出。json 解析不出 = 该工序没完成，抛错而不是给个空壳。"""
    text = str(reply or "").strip()
    if stage_def.output == "text":
        if not text:
            raise ValueError("工序没有任何输出")
        return {"text": text}
    if not text:
        raise ValueError("工序没有任何输出（本工序声明 output: json）")
    for candidate in ([m.group(0)] if (m := _OBJECT.search(text)) else []) + [text]:
        try:
            data = json.loads(candidate)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            return data
    raise ValueError("工序未按约定输出 JSON 对象（若本工序本就该产出自由文本，"
                     "请在流水线定义里写 output: text）")


def build_stage_executor(
    repo_root: str,
    *,
    llm: Any = None,
    confirm: Optional[Callable] = None,
    on_progress: Optional[Callable[[str], None]] = None,
    capabilities: Any = None,
    max_steps: int = 12,
) -> Callable:
    """造一个可注入 `pipeline.advance()` 的执行器。

    `confirm` 是唯一的对外动作闸门；无人值守场景传 None，outbound 工序会被 fail-closed 拒绝。
    """
    log = on_progress or (lambda _m: None)

    async def execute(stage_def: StageDef, inputs: List[Product]) -> Dict[str, Any]:
        from src.agents.main_agent import build_read_tools, build_subagent
        from src.agents.agent_loop import MainAgent
        from src.agents.subagents import registry_for
        from src.agents.taint import is_tainted, mark_tainted, merge_nested_taint
        from src.llm.client import usage_scope

        inherited = [p for p in inputs if p.tainted]
        reasons = "；".join(dict.fromkeys(p.taint_reason for p in inherited if p.taint_reason))

        if stage_def.outbound:
            # 对外动作必须过人闸。没有确认通道就拒绝——无人值守下"没人能说不"不等于"可以做"。
            if confirm is None:
                raise RuntimeError(
                    f"工序「{stage_def.id}」有对外动作，当前入口没有确认通道——已拒绝"
                    f"（fail-closed）。请在带确认的入口（TUI/Web/IM/--yes）推进。")
            warning = f"\n⚠ 本工序的输入含外部摄入内容（{reasons or '来源见产出物血缘'}）" if inherited else ""
            if not await confirm(f"流水线「{stage_def.id}」要执行对外动作"
                                 f"（产出 {stage_def.produces or '未声明'}）。{warning}"):
                raise RuntimeError(f"已取消：用户未放行工序「{stage_def.id}」的对外动作。")

        spec = registry_for(repo_root).get(stage_def.role) if stage_def.role else None
        if stage_def.role and spec is None:
            raise RuntimeError(f"工序「{stage_def.id}」声明的角色 {stage_def.role!r} 不存在"
                               f"（在 .vortocode/agents/{stage_def.role}.md 定义它）")

        shape = ("最终**只输出一个 JSON 对象**作为本工序的产出（可含 summary 字段作一句话摘要）；"
                 "不要输出解释性文字。" if stage_def.output != "text"
                 else "最终输出本工序的成文内容。")
        prompt = (f"你正在执行流水线工序「{stage_def.id}」。\n"
                  f"{stage_def.note}\n\n【上游产出物】\n{_render_inputs(inputs)}\n\n"
                  f"【产出要求】{shape}")

        if spec is not None:
            sub = build_subagent(repo_root, spec, llm=llm, confirm=confirm,
                                 on_progress=on_progress, capabilities=capabilities,
                                 with_web=stage_def.web)
        else:
            base = build_read_tools(repo_root)
            if stage_def.web:
                from src.agents.tools.web import build_web_tools
                base = base + build_web_tools()
            sub = MainAgent(base, llm=llm, max_steps=max_steps,
                            extra_system="你是流水线工序执行者，按要求产出，不做职责外的事。",
                            capabilities=capabilities)

        log(f"▶ 工序 {stage_def.id}" + (f"（角色 {stage_def.role}）" if stage_def.role else ""))
        with usage_scope() as usage:                       # 分段计量：哪道工序贵，得答得出
            with merge_nested_taint() as nested:
                if inherited:
                    # 输入带污点 → 本回合就是在处理不可信内容，工序内部的外发工具必须看得见。
                    mark_tainted()
                reply = await sub.run_turn(prompt, mode="build" if stage_def.outbound else "plan")

        data = _parse_output(reply, stage_def)
        summary = str(data.pop("summary", "") or "").strip()
        self_tainted = bool(nested.child_tainted) and not inherited
        return {
            "payload": data,
            "summary": summary or f"{stage_def.id} 产出",
            "tainted": bool(nested.child_tainted) or is_tainted(),
            "taint_reason": "本工序摄入了外部内容（网页/搜索/MCP）" if self_tainted else "",
            "tokens": int(usage.get("total_tokens") or 0),
        }

    return execute
