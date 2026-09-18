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
                  untrusted_input: bool = False, with_dev: bool = True,
                  extra_system: str | None = None, trust_level: str | None = None):
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
    # trust_level：用户为这个会话选的授权档位（见 agents/trust.py）。内核按能力档案再夹一次，
    # 所以无人值守拿不到免确认、外部会话拿不到写/执行的空白支票；污点回合则一律作废。
    gated_confirm = make_confirm_gate(confirm, auto_approve=auto_approve,
                                      can_ask_human=(can_ask_human and confirm is not None),
                                      on_decision=on_decision, trust_level=trust_level,
                                      capability_profile=profile)
    # plan 模式下模型请求动手 → 当场问用户要授权（真机 2026-07-27：此前 on_escalate **只有 TUI
    # 接了**，Web/CLI/IM 一律 None，于是 request_build 返回"请让用户手动切"，模型照着转述成
    # "请按 Tab 键"——钉钉聊天窗口里根本没有 Tab 键）。
    #
    # 走 `gated_confirm` 而不是裸 confirm，是因为这条**恰恰是最需要污点保护的一条**：展示给人的
    # reason/next_action 是**模型给的**，而模型可能刚读过攻击者的网页。内核门会带上 D0 防注入
    # 横幅、并让污点回合下的一切免确认授权失效。fail-closed 同样由内核保证（问不到人就是拒）。
    #
    # 刻意**只升本回合**（MainAgent 每轮重置 `_escalated`），不把端的 mode 永久改成 build：
    # 用户要的是"每次动手前问我一句"，而不是一次点头换来长期写权限。
    async def _escalate(name: str, args: dict) -> bool:
        reason = str(args.get("reason") or "").strip()
        nxt = str(args.get("next_action") or "").strip()
        if name == "request_build":
            parts = ["plan 阶段分析已完成，需要你授权才能动手。"]
        else:
            parts = [f"这一步要用写/重型工具「{name}」，plan(只读)模式下不可用。"]
        if reason:
            parts.append(f"原因：{reason}")
        if nxt:
            parts.append(f"下一步：{nxt}")
        parts.append("授权执行本回合的后续操作？（仅本回合，不改变你的默认模式）")
        return bool(await gated_confirm("\n".join(parts)))

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
                                  with_dev=with_dev,   # 研究员档：不给改主项目代码/落分支/开 PR 的工具
                                  # 排班面与 dev 面同档开关：两者都是**主人的运维面**，研究员
                                  # （给同事用的资料助理）两样都不该有。将来若出现"要 dev 不要
                                  # cron"的档，把这里拆成独立参数即可——工厂那侧本就是两个参数。
                                  with_cron=with_dev,
                                  on_diff=on_diff)   # 确认前的结构化 diff 推送（AGENT_DIFF，端可不接）
        if workspace_scope == SCRATCH:
            tools.append(workspace_tool)  # Scratch 仍可声明需要用户真实项目，而不是猜路径
    # 各端**永久**切 build 的真实方式。别让内核去猜，也别在系统提示里写死某一个端的键。
    _SWITCH_HINT = {"im": "回复 `/mode build`", "web": "点界面上的 plan/build 开关",
                    "cli": "重跑时加 `-b` 参数"}
    kwargs = dict(plan_tool=True, permissions=load_permissions(repo_root),
                  on_escalate=_escalate,          # plan→build 授权：三端同一份内核判定
                  mode_switch_hint=_SWITCH_HINT.get(kind, ""),
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
    if extra_system:
        # 调用方注入的**静态**片段（如"服务对象是谁"的人设）。必须静态：system prompt 要在
        # 同一会话内字节级稳定，否则破坏上游前缀缓存命中（见 MainAgent._system 的说明）。
        parts.append(str(extra_system))
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


def capability_update_note(saved_tools, current_tools, saved_fp=None, current_fp=None) -> str | None:
    """比对「存盘时的工具清单」与「本次装配的清单」，有出入就生成一条能力更新通告。

    为什么要有（真机 2026-07-27）：#243 把 screenshot_page 接上并部署后，钉钉里再要截图
    **仍被拒**——会话历史跨重启持久化，里面躺着升级前那句（当时如实的）「我没有浏览器截图
    功能」，模型对自己说过话的一致性压过了系统提示与工具清单，把过期否认逐字复读（连推荐的
    第三方工具名都一样）。提示词治不了：#233 的「不许凭印象否认」就在系统提示里，照样输给
    上下文先例。修法是把「清单变了」**作为事实注入历史**，让新旧两句话在上下文里正面相遇。

    镜像方向同样要防：研究员档复原一份满配旧会话时，历史里它「跑过 dev_auto」，不提示「移除」
    它就会许诺自己已经没有的能力。

    saved_tools 为 None = 老会话早于清单记录机制，给不出具体 diff → 退化为通用提醒。
    返回 None = 清单没变，什么都不用注。
    """
    cur = sorted({str(t) for t in (current_tools or [])})
    if not cur:
        return None                                   # 连当前清单都拿不到就别装懂
    tail = ("本会话此前关于「能做什么 / 做不到什么」的说法——包括你自己说过的「做不到」——"
            "可能已过时；回答能力问题前，以当前工具清单为准。")
    if saved_tools is None:
        return f"[能力更新] 服务已升级（本会话存档早于工具清单记录，列不出具体差异）。{tail}"
    old = {str(t) for t in saved_tools}
    added = sorted(set(cur) - old)
    removed = sorted(old - set(cur))
    # 存档**缺少一个我们现在会记录的字段**，本身就是"升级过"的证据，不是猜测：那个字段是被
    # 某次升级加进来的，老档没有它只能说明它存于那次升级之前。所以这里判"行为变了"。
    # （真机 2026-07-27：#248 给会话加了指纹，可当时那个中毒会话的存档只有 tool_names、
    #   清单又恰好没变，于是通告静默通过——历史里三条"请按 Tab"继续毒着。）
    # 只会触发一次：下次存盘就带上指纹了，之后走正常比对。
    behavior_changed = (bool(saved_fp and current_fp and saved_fp != current_fp)
                        or (saved_fp is None and current_fp is not None))
    if not added and not removed:
        # 工具清单没动，但**行为契约变了**也要通告（真机 2026-07-27：#247 只改规则与工具描述，
        # 清单一个没动，于是这里静默通过，而历史里三条旧回复还在教用户"按 Tab"，被模型照抄）。
        if not behavior_changed:
            return None
        return f"[能力更新] 服务已升级，行为规则/工具说明有变（工具清单未变）。{tail}"

    def _fmt(names: list) -> str:
        return "、".join(names[:8]) + (f" 等{len(names)}个" if len(names) > 8 else "")

    parts = ([f"新增：{_fmt(added)}"] if added else []) + \
            ([f"移除：{_fmt(removed)}"] if removed else []) + \
            (["行为规则亦有更新"] if behavior_changed else [])
    return f"[能力更新] 服务已升级，工具清单有变——{'；'.join(parts)}。{tail}"


def inject_capability_note(agent, saved) -> bool:
    """会话从磁盘复原**之后**调用：清单有变 → 把通告追加进 history，返回是否注了。

    只会注一次：通告随会话正常持久化，且下次存盘会带上新清单，重启后 diff 为空；
    注入后一直没跑过回合（没存盘）也收敛——同一份磁盘档每次重启生成同一条通告。
    空历史不注（没有旧话可过期）。任何异常吞掉——通告是尽力而为，不许影响会话可用性。
    """
    try:
        history = getattr(agent, "history", None)
        tools = getattr(agent, "tools", None)
        if not history or not isinstance(tools, dict) or not tools:
            return False
        fp = getattr(agent, "behavior_fingerprint", None)
        note = capability_update_note((saved or {}).get("tool_names"), tools.keys(),
                                      (saved or {}).get("behavior_fp"),
                                      fp() if callable(fp) else None)
        if not note:
            return False
        history.append({"role": "user", "content": note})
        return True
    except Exception:  # noqa: BLE001
        return False


def session_tool_names(agent) -> list | None:
    """存盘用：agent 的工具名清单；读不到就 None（**别报空清单**——None 与 [] 是两种事实）。"""
    tools = getattr(agent, "tools", None)
    return sorted(tools) if isinstance(tools, dict) and tools else None


def session_behavior_fp(agent) -> str | None:
    """存盘用：行为契约指纹；agent 不支持（协议桩/降级装配）就 None。"""
    fp = getattr(agent, "behavior_fingerprint", None)
    if not callable(fp):
        return None
    try:
        return str(fp())
    except Exception:  # noqa: BLE001 —— 指纹是旁路，算不出来不该影响存盘
        return None
