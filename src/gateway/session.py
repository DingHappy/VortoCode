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
    from src.agents.main_agent import (MainAgent, build_agent_tools, make_confirm_gate,
                                       native_default, skill_catalog)
    from src.agents.capabilities import SessionCapabilities, UNATTENDED_PROFILE
    from src.agents.permissions import load_permissions

    # 走内核的统一确认门：无人值守 = 问不到人 + 未授权自动放行 → 一切需要确认的操作都拒。
    # 语义与旧的 _deny 相同，但从此**新加的规矩自动继承**（不再是各端各写一遍）。
    # 不传 ask_human：`can_ask_human=False` 时 gate 的 decide() 直接判 DENY、根本不会去问
    # （fail-closed 就在 gate 里，不靠这里塞一个"一律拒"的哨兵回调）。
    confirm = make_confirm_gate(auto_approve=False, can_ask_human=False)
    capabilities = SessionCapabilities.for_profile(UNATTENDED_PROFILE, repo_root)
    tools = build_agent_tools(
        repo_root, confirm=confirm, with_artifacts=False, memory_source="isolated",
        capabilities=capabilities,
        # **无人值守不给出网工具**（自审逮到的真洞）：web_fetch 是 read_only、不过确认门，
        # 而 GET 的 query string 就是一条外传通道。无人值守下系统提示可能被本地文件
        # （repo.md / BACKLOG.md / HEARTBEAT.md）污染——一旦模型被诱导去 fetch 攻击者的 URL，
        # 就是**零人工介入的静默外传**。而无人值守本来也不需要出网（领 BACKLOG 干活、跑评测都不用）。
        with_web=False,
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
    # 仓库记忆**连轻上下文也带**（不受 light 影响）：它正是为"流水线每进一个仓库都失忆"而立的项，
    # 而 heartbeat 领 BACKLOG 干活恰恰是最需要它的场景——构建怪癖、能跑的测试命令、踩过的坑。
    # 上限只有 2000 字符，比省掉它的收益划算得多。（B5-7 漏接了这条路径。）
    from src.agents.repo_memory import load_repo_memory
    repo_mem = load_repo_memory(repo_root)
    if repo_mem:
        parts.append(repo_mem)
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
