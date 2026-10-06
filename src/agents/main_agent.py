"""Client-neutral agent assembly and backwards-compatible public imports.

The loop lives in agent_loop, development in dev_tools, and role/delegation
assembly in delegation. Factories are explicit injection seams, not reverse imports.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from src.agents.tool import Tool
from src.agents.gate import make_confirm_gate  # noqa: F401
from src.utils.exc_utils import _exc_text  # noqa: F401

from src.agents.agent_loop import (  # noqa: F401,E402
    MainAgent,
    SYSTEM_TEMPLATE,
    DEV_SUBAGENT_ROLE,
    # 模块级常量也要一并再导出：测试直接 import 它们来断上下文预算/折叠策略
    # （漏了两个当场被 test_main_agent.py 的 ImportError 抓住）。这里是**照
    # agent_loop 的模块级名字全量对齐**补的，不是逐个撞红了再加。
    _CONTEXT_POLICY_PROFILES,
    _CONTEXT_WINDOW_FRACTION,
    _CONTEXT_MIN_WINDOW_TO_SCALE,
    _CONTEXT_BUDGET_HARD_CAP,
    _TRIM_LOW_WATERMARK,
    _MAX_COMPACT_FOCUS,
    _FOLD_MARK,
    _FOLD_KEEP_RECENT_TOOLS,
    _FORCE_FINISH_RULE,
    _SUMMARY_SYSTEM,
    _NUDGE,
    parse_tool_call,
    parse_tool_calls,
    native_default,
    _env_num,
    _env_int,
    _env_block,
    _clip_middle,
    _fmt_args,
    _tool_catalog,
    _tool_results_msg,
    _split_tool_results,
    _to_native_messages,
    _is_weak_final,
    _strip_fences,
    _first_json_object,
    _normalize_context_policy,
    _native_error_is_permanent,
)

# Compatibility exports for existing clients. Providers import leaf modules directly.
from src.agents.tools._common import (  # noqa: F401,E402
    _MAX_TOOL_RESULT_DEFAULT,
    _MAX_READ_FILE_DEFAULT,
    _env_limit,
    _max_tool_result,
    _max_read_file,
    _MAX_IMAGE_BYTES_DEFAULT,
    _max_image_bytes,
    _image_exts,
    _truthy,
)
from src.agents.tools.files import (  # noqa: F401,E402
    _glob_to_regex,
    _resolve_within,
    build_confirmed_write_tools,
    build_read_tools,
    build_test_tool,
    build_write_tools,
)
from src.agents.tools.web import (  # noqa: F401,E402
    build_web_tools,
    build_screenshot_tool,
)
from src.agents.tools.cron import (  # noqa: F401,E402
    build_cron_tools,
)
from src.agents.tools.media import (  # noqa: F401,E402
    build_im_media_tools,
)
from src.agents.tools.shell import (  # noqa: F401,E402
    build_command_tool,
    build_pr_tool,
)
from src.agents.tools.memory import (  # noqa: F401,E402
    _longterm_store,
    build_memory_tools,
)
from src.agents.tools.skills import (  # noqa: F401,E402
    LoadedSkill,
    SkillRegistry,
    _skill_registry_for,
    skill_catalog,
    build_skill_tools,
)


from src.agents.dev_policy import (  # noqa: F401
    _dev_review_enabled,
    _dev_attempts,
    _dev_parallelism,
    preflight_dev,
    land_note,
    _repair_prompt,
    _noop_retry_prompt,
    _missing_tests_retry_prompt,
    _dropped_tests_retry_prompt,
    _fail_note,
    _noop_note,
    _log_stage_usage,
    _remote_has_branch,
    _detect_base_branch,
    _has_remote,
    _remote_default_branch,
    _is_test_path,
    _count_test_files,
    _WANTS_TESTS_RE,
    _REMOVED_TEST_RE,
    _ADDED_TEST_RE,
    _DROP_VERB,
    _MAY_DROP_TESTS_RE,
    _removed_test_funcs,
    _dropped_in_chunk,
    _dropped_tests,
    _dropped_tests_note,
    _wants_new_tests,
    _branch_changed_files,
    _test_delta_msg,
    _test_delta_note,
    _diff_change_counts,
)
from src.agents import dev_tools as _development, delegation as _delegation
from src.agents.delegation import research_parallel_cap  # noqa: F401

def build_dev_tools(repo_root, on_progress=None, confirm=None, draft_pr=False,
                    capabilities=None, on_diff=None, question_factory=None):
    return _development.build_dev_tools(
        repo_root, on_progress=on_progress, confirm=confirm, draft_pr=draft_pr,
        capabilities=capabilities, on_diff=on_diff, agent_factory=MainAgent,
        question_factory=question_factory)


def build_subagent(repo_root, spec, *, llm=None, confirm=None, on_progress=None,
                   capabilities=None, with_web=False):
    return _delegation.build_subagent(
        repo_root, spec, llm=llm, confirm=confirm, on_progress=on_progress,
        capabilities=capabilities, with_web=with_web,
        agent_factory=MainAgent, dev_factory=build_dev_tools)


def build_research_tools(repo_root, *, llm=None, max_steps=12, max_parallel=5,
                         default_parallel=2, confirm=None, on_progress=None,
                         capabilities=None, collaboration=None, on_child_tool=None,
                         answer_task_question=None, release_task_dependencies=None, reconcile_task_dependencies=None):
    return _delegation.build_research_tools(
        repo_root, llm=llm, max_steps=max_steps, max_parallel=max_parallel,
        default_parallel=default_parallel, confirm=confirm, on_progress=on_progress,
        capabilities=capabilities, agent_factory=MainAgent,
        subagent_factory=build_subagent, collaboration=collaboration, on_child_tool=on_child_tool,
        answer_task_question=answer_task_question, release_task_dependencies=release_task_dependencies,
        reconcile_task_dependencies=reconcile_task_dependencies)


def build_agent_tools(repo_root: str, *, confirm, on_progress: Optional[Callable[[str], None]] = None,
                      with_artifacts: bool = False, draft_pr: bool = False,
                      memory_source: str = "agent", memory_session_id=None,
                      capabilities: Any = None, with_web: bool = True,
                      # 出站投递面（send_image/send_file）。默认跟随 with_web 保持既有行为；
                      # 单独可控是因为它们与"读外网"是**两件事**：那两个要过确认门，无人值守
                      # 问不到人必拒——给了只会让模型反复撞一堵必然拒绝的墙。
                      with_im_media: Optional[bool] = None,
                      with_dev: bool = True, with_cron: bool = True,
                      task_owner=None, on_task_update=None, answer_task_question=None, release_task_dependencies=None,
                      reconcile_task_dependencies=None,
                      on_diff: Optional[Callable[[str, str], None]] = None) -> list[Tool]:
    """标准主 agent 工具集（headless CLI 与 Web /agent 共用，保证二者"同源"、不漂移）。

    此前 cli._build_headless_agent 与 web._new_agent 各自手写同一串 build_*，极易漂移
    （工具清单/顺序/confirm 语义不一致）。收敛到这里一处装配：
      read（行段/grep/glob/git 只读/语义导航）+ research（只读子 agent 委派）+ web（fetch/search）
      + memory（跨会话长期记忆）+ skill（use_skill/save_skill）
      [+ artifact（发布/列制品，仅 with_artifacts）] + dev（隔离实现/并行，绿落 vorto 分支）
      + command（run_command）+ pr（open_pr）。
    confirm: async (message)->bool 确认门。**必须是 make_confirm_gate 包过的**（见 build_session）
      ——"要不要问、能不能免"由内核判，端只负责怎么问人。别再往这里塞裸 confirm。
    on_progress: dev 流水线进度回调（长任务边跑边播）。
    with_artifacts: 是否含制品工具（Web 有查看页故开；headless CLI 无浏览器故关）。
    with_web: 是否含联网工具。**无人值守会话必须传 False** —— web_fetch 是 read_only、不过确认门，
      而 GET 的 query string 就是一条外传通道；无人值守下系统提示可能被本地文件（repo.md /
      BACKLOG.md / HEARTBEAT.md）污染，一旦模型被诱导去 fetch 攻击者的 URL，就是零人工介入的
      静默外传。无人值守本来也不需要出网（领 BACKLOG 干活、跑评测都不用）。
    TUI 不走本工厂——它用富 UI 版写/dev/command 工具（着色 diff + ConfirmScreen），刻意不同源。
    注：调用方（CLI/Web）应把 `skill_catalog(repo_root)` 注入 extra_system，模型才知道有哪些技能可 use_skill。
    """
    import uuid
    from src.gateway.collaboration import CollaborationService
    owner = task_owner or memory_session_id or f"agent-{uuid.uuid4().hex}"
    collaboration = CollaborationService(repo_root, owner, on_update=on_task_update)
    tools = (build_read_tools(repo_root)
             + build_research_tools(repo_root, confirm=confirm, on_progress=on_progress,
                                    capabilities=capabilities, collaboration=collaboration,
                                    answer_task_question=answer_task_question, release_task_dependencies=release_task_dependencies,
                                    reconcile_task_dependencies=reconcile_task_dependencies)
             + build_memory_tools(repo_root, confirm, source=memory_source,
                                  session_id=memory_session_id)
             + build_skill_tools(repo_root, confirm))
    if with_web:
        tools += build_web_tools() + build_screenshot_tool(repo_root)
    if with_artifacts:
        from src.web.artifacts import build_artifact_tools    # 惰性导入：避免 agents 层在导入期硬依赖 web

        # 制品是**对外发布**（写盘 + 经 /artifact/<id> 提供服务）、删除不可逆 → 必须过确认门。
        # 此前这里根本没传 confirm，publish/delete 完全绕过了 gate（自审逮到）。
        # artifact 的 confirm 签名是 (preview, is_update)，这里适配成内核 gate 的 (message)。
        async def _art_publish(preview: dict, is_update: bool) -> bool:
            # "首次发布问、清白更新静默、污点更新仍问"的策略**统一在 build_artifact_tools._publish 里**
            # （工具边界，覆盖所有端）。这里只做"被调到就过内核 gate"——不再各写一遍污点判定。
            what = "更新" if is_update else "发布"
            return bool(await confirm(
                f"{what}制品「{preview.get('title') or preview.get('id')}」？"
                f"它会被写盘并经 /artifact/ 对外提供访问。"))

        async def _art_delete(preview: dict) -> bool:
            return bool(await confirm(
                f"删除制品「{preview.get('title') or preview.get('id')}」？此操作不可逆。"))

        tools += build_artifact_tools(repo_root, confirm=_art_publish,
                                      confirm_delete=_art_delete)
    if with_web if with_im_media is None else with_im_media:
        tools += build_im_media_tools(repo_root, confirm)
    if with_cron:
        # **无人值守必须传 False**：cron 作业能创建 cron 作业 = 自我复制驻留，等于 agent 可以
        # 自授「周期性无人值守执行」这项权限。研究员档同样不给（那是运维面，不是资料助理的活）。
        tools += build_cron_tools(repo_root, confirm)
    if with_dev:
        # 直写工具（逐次确认 + 先给 diff）与隔离流水线**并存、各管一段**：小改动当场改、
        # 当场看 diff；大任务仍走 worktree 实现+自测+落 vorto 分支。此前只有后者，于是三行的
        # 改动也要跑几分钟流水线，流水线一受挫模型就绕去 run_command 里拼 sed/python 改文件
        # （2026-09-17 真机诊断）。写盘判定仍由内核确认门说了算：污点回合一律回到真人拍板。
        tools += build_confirmed_write_tools(repo_root, confirm, on_diff=on_diff)
        tools += (build_dev_tools(repo_root, on_progress=on_progress, confirm=confirm,
                                  draft_pr=draft_pr, capabilities=capabilities, on_diff=on_diff)
                  + build_pr_tool(repo_root, confirm))
    else:
        # 没有 dev 流水线的档（研究员）**必须另给写路径**：serve 侧刻意没有直写工具，
        # 写操作一律走隔离流水线——把流水线砍掉却不补，就等于连写个抓取脚本都做不到，
        # 那句"可以写代码来更好地帮助收集资料"就成了空话。
        # build_write_tools 是**根限定**的（`..` 越界拦死），而这一档的 repo_root 就是它自己的
        # 沙盒工作区，不是主项目——所以"在自己家里随便写"是安全的，无需逐次确认
        #（与一次性 worktree 里的子 agent 同一个道理：改动只落在自己的地盘）。
        tools += build_write_tools(repo_root)
    # run_command 与 dev 分开：研究员**要**能跑自己写的抓取/清洗脚本（沙箱内），
    # 但不该有改主项目代码、落分支、开 PR 的能力。把两者绑在一起会逼人二选一：
    # 要么给全套（权限过大），要么连脚本都跑不了（等于废了"写代码辅助收集资料"）。
    tools += build_command_tool(repo_root, confirm)
    return tools
