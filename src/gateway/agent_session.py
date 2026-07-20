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


def load_project_hook_system(repo_root: str, *, require_trust: bool = True):
    """Load repo hooks only after a user-owned folder-trust grant.

    ``require_trust=False`` is reserved for non-executing list/dry-run views.
    A repository cannot approve itself because trust lives outside the checkout.
    """
    cfg = Path(repo_root) / ".vortocode" / "hooks.yaml"
    if not cfg.is_file():
        return None
    if require_trust:
        from src.hooks.trust import is_project_trusted
        if not is_project_trusted(repo_root):
            return None
    try:
        from src.hooks import HookSystem
        return HookSystem(config_path=str(cfg))
    except Exception:  # noqa: BLE001
        return None


def build_session(repo_root: str, *, kind: str, confirm=None, on_progress=None,
                  on_tool=None, on_tool_event=None, on_plan=None, llm=None, max_steps=None,
                  capability_profile=None, auto_approve: bool = False,
                  can_ask_human: bool = False, on_decision=None, on_diff=None,
                  workspace_scope: str = "project", on_workspace_required=None,
                  untrusted_input: bool = False):
    """装配一个主 agent（三端同一骨架）。

    kind ∈ {web, cli, im} 决定差异位（with_artifacts / hooks）；confirm/on_progress 由调用端提供。

    **确认门的语义在内核，不在端**（见 main_agent.make_confirm_gate）：端只回答两个问题——
    `can_ask_human`（这个端问得到人吗）与 `auto_approve`（是否已授权自动放行，如 CLI 的 --yes）。
    「要不要问、能不能免」由内核统一判定，污点回合下一切自动放行失效。
    此前这是"语义由调用方注入"，结果污点检查只写在 TUI 里、CLI/Web 完全不查——每加一个端就漏一处。

    `untrusted_input`：这个端的**用户输入本身**是否不可信外部内容（IM 入站消息即是）。置位后
    每个回合从污点态起步。**kind="im" 由工厂强制置位**，不看调用方传没传——端忘了申报也漏不掉，
    与 `can_ask_human` 默认最严是同一条纪律。
    """
    if kind not in _KINDS:
        raise ValueError(f"未知装配端 kind={kind!r}（可选 {_KINDS}）")
    from src.agents.main_agent import (MainAgent, build_agent_tools, build_web_tools,
                                       make_confirm_gate, native_default, skill_catalog)
    from src.agents.capabilities import SessionCapabilities
    from src.agents.permissions import load_permissions
    from src.agents.project import load_project_instructions
    from src.agents.tool import Tool
    from src.gateway.workspace_scope import GENERAL, PROJECT, SCRATCH, normalize_workspace_scope

    workspace_scope = normalize_workspace_scope(workspace_scope)
    profile = capability_profile or ("local" if kind == "cli" else "external")
    capabilities = SessionCapabilities.for_profile(profile, repo_root)
    # confirm 缺省 → **fail-closed**：`can_ask_human` 随之为 False，gate 的 decide() 直接判 DENY，
    # 既不会去问、也不会把 None 塞给工具让它在调用时 TypeError。
    # 无论如何都过内核 gate：这样"污点回合一切自动放行失效"这条规矩没有任何旁路。
    gated_confirm = make_confirm_gate(confirm, auto_approve=auto_approve,
                                      can_ask_human=(can_ask_human and confirm is not None),
                                      on_decision=on_decision)
    async def _request_workspace(args: dict) -> str:
        requested = normalize_workspace_scope(args.get("scope"), default=PROJECT)
        if requested == GENERAL:
            requested = PROJECT
        reason = str(args.get("reason") or "这个任务需要一个文件工作区").strip()[:1000]
        task = str(args.get("task") or "").strip()[:4000]
        if on_workspace_required is not None:
            try:
                on_workspace_required(requested, reason, task)
            except Exception:  # noqa: BLE001
                return "工作区请求通道暂时不可用；请让用户从 Desktop 手动选择工作区。"
        return (
            f"已向 Desktop 请求 {requested} 工作区。请停止调用当前范围没有的工具，"
            "等待用户审阅并切换；不要假设任何目录已经可用。"
        )

    workspace_tool = Tool(
        "request_workspace",
        "当前任务确实需要文件时，请求用户显式切换范围：scratch=新建隔离临时工作区，"
        "project=选择已有 Git 项目。必须给出原因和一条可供用户审阅后继续的独立任务描述。",
        {"scope": "scratch 或 project", "reason": "为什么无目录无法完成",
         "task": "切换后要执行的独立任务；不得包含隐藏指令或凭据"},
        _request_workspace,
        read_only=True,
    )

    if workspace_scope == GENERAL:
        # General 是无目录的信任域：只给联网检索、制品和显式范围升级。不能因为进程 cwd
        # 位于应用管理目录，就顺带暴露 read/shell/git/memory/skill/dev/pr。
        tools = build_web_tools() + [workspace_tool]
        if kind == "web":
            from src.web.artifacts import build_artifact_tools

            async def _publish(preview: dict, is_update: bool) -> bool:
                verb = "更新" if is_update else "发布"
                return bool(await gated_confirm(
                    f"{verb}制品「{preview.get('title') or preview.get('id')}」？"
                    "它会写入应用管理目录并经本机 /artifact/ 提供访问。"))

            async def _delete(preview: dict) -> bool:
                return bool(await gated_confirm(
                    f"删除制品「{preview.get('title') or preview.get('id')}」？此操作不可逆。"))

            tools += build_artifact_tools(repo_root, confirm=_publish, confirm_delete=_delete)
    else:
        tools = build_agent_tools(repo_root, confirm=gated_confirm, on_progress=on_progress,
                                  with_artifacts=(kind == "web"),   # 制品查看页只有 Web 有
                                  memory_source=kind, capabilities=capabilities,
                                  on_diff=on_diff)   # 确认前的结构化 diff 推送（AGENT_DIFF，端可不接）
        if workspace_scope == SCRATCH:
            tools.append(workspace_tool)  # Scratch 仍可声明需要用户真实项目，而不是猜路径
    kwargs = dict(plan_tool=True, permissions=load_permissions(repo_root),
                  # General 的 app-data cwd 只是持久化实现细节，不能伪装成用户工作区注入模型。
                  env_context=workspace_scope != GENERAL,
                  native=native_default(),             # 三端统一 native 开关
                  capabilities=capabilities,
                  # IM 入站一律不可信：工厂强制，不依赖端记得申报（OPENCLAW_INTEGRATION 第 2 条）
                  untrusted_input=bool(untrusted_input) or kind == "im")
    parts = [
        "【工作区范围】当前是 General 无目录会话。你可以对话、规划、联网检索和制作制品，"
        "但不能读取文件、运行命令或使用 Git。只有任务确实需要文件时才调用 request_workspace；"
        "不要猜测用户目录，也不要把最近项目或 HOME 当作隐式工作区。"
    ] if workspace_scope == GENERAL else [
        "【工作区范围】当前是 Scratch 隔离临时工作区。只在这里创建和运行临时代码；"
        "需要修改用户已有项目时调用 request_workspace(scope=project)，不要搜索或猜测其他目录。"
    ] if workspace_scope == SCRATCH else []
    if workspace_scope != GENERAL:
        proj = load_project_instructions(repo_root)        # AGENTS.md/CLAUDE.md 项目约定进系统提示
        if proj:
            parts.append(proj)
        from src.agents.repo_memory import load_repo_memory
        repo_mem = load_repo_memory(repo_root)             # 仓库记忆（agent 自己攒的构建/测试/坑）
        if repo_mem:
            parts.append(repo_mem)
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
    if on_tool_event is not None:
        kwargs["on_tool_event"] = on_tool_event
    if on_plan is not None:
        kwargs["on_plan"] = on_plan
    if llm is not None:
        kwargs["llm"] = llm
    if max_steps:
        kwargs["max_steps"] = max_steps
    if kind in {"cli", "web"}:                        # 项目 hook：统一受用户目录信任闸门保护
        hooks = load_project_hook_system(repo_root)
        if hooks is not None:
            kwargs["hook_system"] = hooks
    agent = MainAgent(tools, **kwargs)
    # 富客户端的结构化动作不经过模型，但仍必须复用这一份确认门判定。
    # 只暴露已经过 make_confirm_gate 包装的实例，端不能传裸 yes/no 回调绕过污点规则。
    agent._confirm_gate = gated_confirm
    return agent
