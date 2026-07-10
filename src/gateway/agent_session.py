"""gateway 级会话装配（D1 单核化 PR-2）——三端唯一的 MainAgent 装配工厂。

此前 Web(`realtime._new_agent`)/CLI(`cli._build_headless_agent`)/IM(`bridge._build_agent`)
三处各自手写同一串骨架：build_agent_tools → 项目指令 → 技能目录 → permissions → native →
MainAgent——同一串抄三遍就是三端漂移的温床（#115 native 只 TUI 读、#117 TUI 漏传 test_cmd
都是这么来的）。现在骨架只此一份，端差异收敛为 `kind` 的**显式**差异位：

- `web`：带制品工具（有浏览器查看页）；
- `cli`：读 `.vortocode/hooks.yaml`（headless 生命周期钩子，与 TUI 同源）；
- `im`：最小面（无制品、无钩子）。

TUI 的富 UI 装配（约 494 行闭包，着色 diff/ConfirmScreen）**刻意不并**——留到 PR-4
客户端化时对着协议面处理，见 exec-plan-2026-07-b3。
"""

from __future__ import annotations

from pathlib import Path

_KINDS = ("web", "cli", "im")
_SKILL_HEADER = "【可用技能】(需要时用 use_skill 加载其完整指令再执行)"


def _load_cli_hooks(repo_root: str):
    """有 .vortocode/hooks.yaml 才建 HookSystem（headless 专属差异位；坏配置安全降级为无钩子）。"""
    cfg = Path(repo_root) / ".vortocode" / "hooks.yaml"
    if not cfg.is_file():
        return None
    try:
        from src.hooks import HookSystem
        return HookSystem(config_path=str(cfg))
    except Exception:  # noqa: BLE001
        return None


def build_session(repo_root: str, *, kind: str, confirm=None, on_progress=None,
                  on_tool=None, on_plan=None, llm=None, max_steps=None,
                  capability_profile=None):
    """装配一个主 agent（三端同一骨架）。

    kind ∈ {web, cli, im} 决定差异位（with_artifacts / hooks）；confirm/on_progress 由调用端
    提供（holder 重绑等端侧模式留在调用端——工厂只认可调用的 callable）。返回 MainAgent。
    """
    if kind not in _KINDS:
        raise ValueError(f"未知装配端 kind={kind!r}（可选 {_KINDS}）")
    from src.agents.main_agent import (MainAgent, build_agent_tools, native_default,
                                       skill_catalog)
    from src.agents.capabilities import SessionCapabilities
    from src.agents.permissions import load_permissions
    from src.agents.project import load_project_instructions

    profile = capability_profile or ("local" if kind == "cli" else "external")
    capabilities = SessionCapabilities.for_profile(profile)
    tools = build_agent_tools(repo_root, confirm=confirm, on_progress=on_progress,
                              with_artifacts=(kind == "web"),   # 制品查看页只有 Web 有
                              memory_source=kind, capabilities=capabilities)
    kwargs = dict(plan_tool=True, permissions=load_permissions(repo_root),
                  env_context=True,                    # 注入 <env>（cwd/git/日期/目录）
                  native=native_default(),             # 三端统一 native 开关
                  capabilities=capabilities)
    parts = []
    proj = load_project_instructions(repo_root)        # AGENTS.md/CLAUDE.md 项目约定进系统提示
    if proj:
        parts.append(proj)
    catalog = skill_catalog(repo_root)                 # 技能目录进系统提示（模型才知道能 use_skill 什么）
    if catalog:
        parts.append(f"{_SKILL_HEADER}\n{catalog}")
    from src.agents.subagents import subagent_catalog
    agents_cat = subagent_catalog(repo_root)           # 自定义角色目录（task 工具的 agent 参数按名委派）
    if agents_cat:
        parts.append("【可用子 agent】(用 task/research_parallel 的 agent 参数按名委派；"
                     "dev 型角色经隔离流水线写代码、需确认)\n" + agents_cat)
    if parts:
        kwargs["extra_system"] = "\n\n".join(parts)
    if on_tool is not None:
        kwargs["on_tool"] = on_tool
    if on_plan is not None:
        kwargs["on_plan"] = on_plan
    if llm is not None:
        kwargs["llm"] = llm
    if max_steps:
        kwargs["max_steps"] = max_steps
    if kind == "cli":                                  # headless 专属：hooks.yaml 生命周期钩子
        hooks = _load_cli_hooks(repo_root)
        if hooks is not None:
            kwargs["hook_system"] = hooks
    return MainAgent(tools, **kwargs)
