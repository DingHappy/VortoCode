"""隔离会话运行——给 cron / heartbeat 用：全新 MainAgent 跑一回合，不读不写主会话历史。

隔离是 D3 成本/安全控制的地基（照 OpenClaw isolatedSession）：每次心跳/定时都是干净上下文（不吃也不
污染主会话），可指定便宜模型、可只带轻上下文。UI 无关、可注入 llm/agent 便于确定性测试。
"""
from __future__ import annotations

from typing import Optional


async def run_isolated_session(repo_root: str, prompt: str, *, mode: str = "build",
                               model: Optional[str] = None, light: bool = False,
                               extra_system: Optional[str] = None, llm=None,
                               allow_web: bool = False) -> str:
    """在一个全新的、隔离的 MainAgent 会话里跑一回合，返回最终文本。

    light=True：只带 prompt 里给的上下文，不注入项目指令/技能目录（心跳"值班"用，省 token）。
    model：指定便宜模型跑（None 用默认）。绝不落任何会话历史、绝不碰主会话。

    allow_web：**逐个作业**由主人在创建时显式授权的出网许可（cron.yaml 的 `allow_web: true`）。
    默认 False —— 无人值守下 `web_fetch` 是 read_only、不过确认门，而 GET 的 query string 就是
    一条外传通道；系统提示又可能被本地文件（repo.md / BACKLOG.md / HEARTBEAT.md）污染，
    一旦模型被诱导去 fetch 攻击者的 URL 就是零人工介入的静默外传。
    但"每天搜新闻"这类作业确实需要出网，整档一刀切等于这类活儿做不了——所以把边界从**档级**
    细化到**作业级**：谁要出网谁单独申报，且申报那一刻有真人点头（2026-07-27 主人拍板）。
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
    from src.gateway.workspace_scope import GENERAL, current_workspace_scope
    if current_workspace_scope() == GENERAL:
        return await _run_general_isolated(repo_root, prompt, mode=mode, model=model, llm=llm,
                                           allow_web=allow_web, extra_system=extra_system,
                                           capabilities=capabilities, confirm=confirm)
    tools = build_agent_tools(
        repo_root, confirm=confirm, with_artifacts=False, memory_source="isolated",
        capabilities=capabilities,
        # **无人值守不给出网工具**（自审逮到的真洞）：web_fetch 是 read_only、不过确认门，
        # 而 GET 的 query string 就是一条外传通道。无人值守下系统提示可能被本地文件
        # （repo.md / BACKLOG.md / HEARTBEAT.md）污染——一旦模型被诱导去 fetch 攻击者的 URL，
        # 就是**零人工介入的静默外传**。默认关；只有主人在创建作业时逐条授权过的才开（见 docstring）。
        with_web=bool(allow_web),
        # 出站投递面**始终不给**，与 allow_web 无关：send_image/send_file 要过确认门，而这里
        # 问不到人 → 必拒。给了只会让模型反复撞一堵必然拒绝的墙，还误导它以为送得出去。
        # 作业产出本来就走 announce 通道推给主人，不需要它自己发。
        with_im_media=False,
        # **无人值守不给排班工具**（同一条道理的时间版）：cron 作业能创建 cron 作业 = 自我复制
        # 驻留。出网是空间上的外传通道，排班是**时间上的**——让一个没人盯着的回合给自己排下一班，
        # 等于 agent 可以自授"周期性无人值守执行"。而且这里 confirm 恒拒（问不到人），
        # 给了也只会是一堆必然失败的调用。
        with_cron=False,
    )
    parts = []
    if not light:                                    # 非轻上下文才带项目指令 + 技能目录
        from src.agents.project import load_project_instructions, load_user_instructions
        user_instructions = load_user_instructions()
        if user_instructions:
            parts.append(user_instructions)
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
    from src.llm.usage_ledger import usage_source
    with usage_source("定时任务"):
        return await agent.run_turn(prompt, mode=mode, emit=lambda _t: None)


async def _run_general_isolated(repo_root: str, prompt: str, *, mode: str, model: Optional[str], llm,
                                allow_web: bool, extra_system: Optional[str], capabilities, confirm) -> str:
    """常驻助理（通用范围）的无人值守回合：「每天早上帮我…」这类你交代的定时事项。

    通用范围没有工作区：**不给**文件 / 命令 / Git / 记忆写入 / 排班 / 出站投递，一个都不给。
    唯一可能的工具是联网，且只在这条作业创建时由人单独授权过（allow_web）才给。

    带进去的个人资料按「有没有外传通道」分：
    - 不联网的作业：名字、SOUL、关于你、长期记忆摘录都带（它连一条出站路都没有）；
    - 联网的作业：只带名字与 SOUL。网页里的提示注入能诱导模型 fetch 攻击者的 URL，
      GET 的 query 就是外传通道——关于你和记忆不能与它同处一个上下文。
    """
    from src.agents.main_agent import MainAgent, native_default
    from src.agents.permissions import load_permissions
    from src.memory.assistant_profile import assistant_system_parts, load_profile

    tools = []
    if allow_web:
        from src.agents.tools.web import build_web_tools
        tools = build_web_tools()
    else:
        # 不联网的作业可以看本机状态（今天的动态 / 任务单 / 定时事项）——只读、无出站通道
        from src.scheduling.assistant_watch import build_status_tools
        tools = build_status_tools(repo_root)
    if allow_web:
        profile = load_profile(repo_root)
        parts = [f"【你是谁】你是用户的私人助理「{profile['name']}」。"]
        if profile["soul"]:
            parts.append(f"【你的性格与说话方式】{profile['soul']}")
    else:
        parts = assistant_system_parts(repo_root)
    parts.append("【定时事项】这是用户事先交代、到点自动执行的一件事；结果会推给用户。"
                 "如实说做成了什么、没做成什么；没有工具做不到的事直接说明，不要编造。")
    if extra_system:
        parts.append(extra_system)
    kwargs = dict(plan_tool=True, permissions=load_permissions(repo_root), env_context=False,
                  native=native_default(), capabilities=capabilities, extra_system="\n\n".join(parts))
    if llm is not None:
        kwargs["llm"] = llm
    agent = MainAgent(tools, **kwargs)
    if model:
        try:
            agent.set_model(model)
        except Exception:  # noqa: BLE001 —— 设模型失败不影响跑（用默认）
            pass
    from src.llm.usage_ledger import usage_source
    with usage_source("定时任务"):
        return await agent.run_turn(prompt, mode=mode, emit=lambda _t: None)
