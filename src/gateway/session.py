"""隔离会话运行——给 cron / heartbeat 用：全新 MainAgent 跑一回合，不读不写主会话历史。

隔离是 D3 成本/安全控制的地基（照 OpenClaw isolatedSession）：每次心跳/定时都是干净上下文（不吃也不
污染主会话），可指定便宜模型、可只带轻上下文。UI 无关、可注入 llm/agent 便于确定性测试。
"""
from __future__ import annotations

from typing import Optional


async def run_isolated_session(repo_root: str, prompt: str, *, mode: str = "build",
                               model: Optional[str] = None, light: bool = False,
                               extra_system: Optional[str] = None, llm=None) -> str:
    """在一个全新的、隔离的 MainAgent 会话里跑一回合，返回最终文本。

    light=True：只带 prompt 里给的上下文，不注入项目指令/技能目录（心跳"值班"用，省 token）。
    model：指定便宜模型跑（None 用默认）。绝不落任何会话历史、绝不碰主会话。
    """
    from src.agents.main_agent import (MainAgent, build_agent_tools, native_default, skill_catalog)
    from src.agents.capabilities import SessionCapabilities, UNATTENDED_PROFILE
    from src.agents.permissions import load_permissions

    async def _deny(_m):                             # 无人值守：外向操作（push/PR）默认拒绝
        return False

    capabilities = SessionCapabilities.for_profile(UNATTENDED_PROFILE, repo_root)
    tools = build_agent_tools(
        repo_root, confirm=_deny, with_artifacts=False, memory_source="isolated",
        capabilities=capabilities,
    )
    parts = []
    if not light:                                    # 非轻上下文才带项目指令 + 技能目录
        from src.agents.project import load_project_instructions
        proj = load_project_instructions(repo_root)
        if proj:
            parts.append(proj)
        cat = skill_catalog(repo_root)
        if cat:
            parts.append(f"【可用技能】(需要时用 use_skill 加载其完整指令再执行)\n{cat}")
    if extra_system:
        parts.append(extra_system)
    kwargs = dict(plan_tool=True, permissions=load_permissions(repo_root),
                  env_context=True, native=native_default(), capabilities=capabilities)
    if parts:
        kwargs["extra_system"] = "\n\n".join(parts)
    if llm is not None:
        kwargs["llm"] = llm
    agent = MainAgent(tools, **kwargs)
    if model:
        try:
            agent.set_model(model)
        except Exception:  # noqa: BLE001 —— 设模型失败不影响跑（用默认）
            pass
    return await agent.run_turn(prompt, mode=mode, emit=lambda _t: None)
