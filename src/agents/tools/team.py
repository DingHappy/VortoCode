"""主 agent 管任务小组的工具（ADR-014 S1d）：开小组、看进度、回话、验收、停止。

- 开小组会启动成员、花模型用量：按「执行」类过确认门，卡片列出目标、验收、成员与档位。不设额度（2026-11-04）。
- 验收与回话是主人自己的事项（OWN_DATA）：污点回合里照样要问。
- 停止是保护性动作，不问。
- 看进度只给代码拼的结构化视图；协调人写的总结在根带污点时按外部内容给（本回合即为污点）。
- 只有本进程装配了任务调度器（常驻助理）时才挂这些工具。
"""
from __future__ import annotations

import json
from typing import Any, Callable, List, Optional

from src.agents.tool import Tool

_ROLE_TEXT = {"web-researcher": "联网查资料", "reviewer": "只看交给它的材料",
              "developer": "在项目的一次性副本里改代码，不能执行命令、不能联网"}
_ENGINE_TEXT = {"opencode": "OpenCode", "deepseek-harness": "DeepSeek Harness"}


def _card(objective: str, acceptance: List[str], members: List[dict], mode: str, limits: Optional[dict] = None,
          project: Optional[dict] = None) -> str:
    lines = ["开一个任务小组？", f"目标：{objective}", "验收标准："]
    lines += [f"  {index + 1}. {text}" for index, text in enumerate(acceptance)]
    lines.append("成员：" + "、".join(f"{item.get('name')}（{_ROLE_TEXT.get(item.get('role'), item.get('role'))}"
                                      + (f"，由 {_ENGINE_TEXT.get(item['engine'], item['engine'])} 执行"
                                         if item.get("engine") not in (None, "", "vortocode") else "")
                                      + (f"，模型 {item['model']}" if item.get("model") else "") + "）" for item in members))
    if project:
        lines.append(f"项目：{project['name']}（常驻电脑上的 {project['repo_root']}）。开发成员每轮在它的一次性副本里改，"
                     "改完由 VortoCode 在沙箱里跑测试，改动落成本地分支——不推送、不合并，由你审")
    lines.append("档位：" + ("open（成员可联网，小组看不到你的记忆、目标和对话）" if mode == "open"
                            else "private（成员不能联网）"))
    lines.append("不设额度：小组做到交付验收或你停止为止；连续 3 次没有进展会停下来问你")
    return "\n".join(lines)


def _resident_project(name: str) -> Optional[dict]:
    from src.workflow.workbench.resident_projects import load_resident_projects
    wanted = name.strip().lower()
    return next((item for item in load_resident_projects() if item["name"].lower() == wanted), None)


def build_team_tools(repo_root: str, *, confirm: Any, owner: "str | Callable[[], Any]",
                     allow_developers: bool = False) -> List[Tool]:
    """``allow_developers``：只有 Desktop 会话能开带开发成员的小组（批准钥匙 #723 合并前，手机与 IM 都不行）。"""
    from src.agents.gate import request
    from src.agents.taint import mark_tainted
    from src.agents.trust import EXECUTE, OWN_DATA
    from src.scheduling.task_notices import notify_transition
    from src.scheduling.team_runner import get_team_runner
    from src.workflow.tasks.errors import CollaborationConflict
    from src.workflow.tasks.team import DEFAULT_LIMITS, LIVE, TeamService, normalize_limits

    def service() -> TeamService:
        return TeamService(repo_root, on_update=lambda task: notify_transition(repo_root, task))

    def runner():
        live = get_team_runner()
        if live is None:
            raise CollaborationConflict("这台机器上没有任务小组调度器（只在常驻助理上运行）")
        return live

    def guarded(action):
        async def handler(args: dict) -> str:
            try:
                return await action(args)
            except CollaborationConflict as error:
                return f"未执行：{error}"
        return handler

    async def propose(args: dict) -> str:
        runner()
        objective = str(args.get("objective") or "").strip()
        acceptance = [str(item) for item in args.get("acceptance") or []]
        members = [item for item in args.get("members") or [] if isinstance(item, dict)]
        mode = str(args.get("mode") or "open")
        limits = normalize_limits({key: value for key, value in (args.get("limits") or {}).items() if key in DEFAULT_LIMITS})
        project = None
        if any(item.get("role") == "developer" for item in members):
            if not allow_developers:
                return "未执行：带开发成员的小组只能在 Desktop 上开（手机和 IM 里不行）。请主人到 Desktop 上再提。"
            name = str(args.get("project") or "").strip()
            project = _resident_project(name) if name else None
            if project is None:
                from src.workflow.workbench.resident_projects import load_resident_projects
                names = "、".join(item["name"] for item in load_resident_projects()) or "（没有登记任何项目）"
                return f"未执行：开发成员要指定一个登记过的常驻项目 project。可选：{names}"
        card = _card(objective, acceptance, members, mode, limits, project)
        if not await request(confirm, card, EXECUTE):
            return "主人没有同意开这个小组；不要重复提议同一内容，问清楚要怎么改。"
        # Desktop 会话传进来的是取当前会话 ID 的函数（同派活工具）；真机 2026-11-04 曾把函数原样存盘，存不进去
        session = str((owner() if callable(owner) else owner) or "")
        root = service().create(session, objective=objective, acceptance=acceptance, members=members, mode=mode,
                                limits=limits, project=project)
        runner().wake(root.id, "任务刚开始：先拆解，再给成员派活")
        return (f"小组已开工：{root.id}。成员交回结果时由小组自己推进；需要主人决定、交付验收或失败时会通知。"
                "现在简短告诉主人小组开工了，不要替小组做研究。")

    async def overview(_args: dict) -> str:
        live = service()
        roots = [root for root in live.list() if root.status in LIVE][:10]
        if not roots:
            return "现在没有进行中的任务小组。"
        return json.dumps([live.view(root) for root in roots], ensure_ascii=False)

    async def open_one(args: dict) -> str:
        live = service()
        root = live.get(str(args.get("team_id") or ""))
        view = live.view(root)
        submission = root.team.get("submission")
        if submission:
            if view["tainted"]:
                mark_tainted()
            view["submission"] = submission
            view["submission_external_content"] = view["tainted"]
        return json.dumps(view, ensure_ascii=False)

    async def reply(args: dict) -> str:
        team_id, text = str(args.get("team_id") or ""), str(args.get("text") or "").strip()
        if not await request(confirm, f"把这句话转给任务小组 {team_id}：{text}", OWN_DATA):
            return "主人没有同意转达。"
        service().owner_reply(team_id, text)
        runner().wake(team_id, "主人回复了")
        return "已转给小组，小组会接着做。"

    async def decide(args: dict) -> str:
        team_id, accept, note = str(args.get("team_id") or ""), bool(args.get("accept")), str(args.get("note") or "")
        verb = "验收通过" if accept else f"退回（{note}）"
        if not await request(confirm, f"任务小组 {team_id}：{verb}？", OWN_DATA):
            return "主人没有确认。"
        root = service().decide(team_id, accept, note)
        if not accept:
            runner().wake(team_id, "主人退回了交付，按意见继续")
        return f"小组 {team_id} 已{'完成' if root.status == 'done' else '退回，继续进行'}。"

    async def stop(args: dict) -> str:
        root = runner().stop(str(args.get("team_id") or ""), str(args.get("reason") or "主人停止"))
        return f"已停止小组 {root.id}；在跑的成员已请求取消。"

    return [
        Tool("team_propose",
             "大任务（需要多个角色、多轮，例如先查资料再审查、比较方案）时提议开一个任务小组，由主人确认后开工。"
             "成员角色可选：web-researcher（联网查资料）、reviewer（审查材料）、developer（在登记项目里改代码，"
             "只能由 opencode 担任，小组须为 private、要给 project，只能在 Desktop 上开）。"
             "执行框架 engine 默认 vortocode；主人要求时可选 opencode 或 deepseek-harness（外部框架，同样只能做该角色允许的事）。"
             "小任务直接自己做，不要开小组。",
             {"objective": "目标", "acceptance": "验收标准列表（1–10 条）",
              "members": "成员列表，每项 {name, role, engine 可选, model 可选}", "mode": "open（默认）或 private",
              "project": "有 developer 成员时必填：常驻电脑上登记的项目名",
              "limits": "可选 {parallel}：同时最多几个成员在跑（1–4，默认 2）"},
             guarded(propose), read_only=False, serial=True,
             argument_schema={'objective': {'type': 'string'},
             'acceptance': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 10},
             'members': {'type': 'array',
                         'items': {'type': 'object',
                                   'properties': {'name': {'type': 'string'},
                                                  'role': {'type': 'string',
                                                           'enum': ['web-researcher',
                                                                    'reviewer',
                                                                    'developer']},
                                                  'engine': {'type': 'string',
                                                             'enum': ['vortocode',
                                                                      'opencode',
                                                                      'deepseek-harness']},
                                                  'model': {'type': 'string'}},
                                   'required': ['name', 'role']}},
             'limits': {'type': 'object',
                        'properties': {'parallel': {'type': 'integer', 'minimum': 1, 'maximum': 4}}},
             'mode': {'type': 'string', 'enum': ['open', 'private']}}, required_args=('objective', 'acceptance', 'members')),
        Tool("team_list", "列出进行中的任务小组：状态、成员、子任务和用量。", {}, guarded(overview), read_only=True),
        Tool("team_open", "查看一个任务小组的详情；交付了的会带上协调人的总结和逐条证据。",
             {"team_id": "小组 ID"}, guarded(open_one), read_only=True, required_args=('team_id',), argument_schema={'team_id': {'type': 'string'}}),
        Tool("team_reply", "把主人的回答转给正在等主人的任务小组。", {"team_id": "小组 ID", "text": "主人的原话"},
             guarded(reply), read_only=False, serial=True, required_args=('team_id', 'text'), argument_schema={'team_id': {'type': 'string'}, 'text': {'type': 'string'}}),
        Tool("team_decide", "主人对小组的交付做决定：accept=true 验收通过，false 退回并写明原因。",
             {"team_id": "小组 ID", "accept": "true/false", "note": "退回原因"}, guarded(decide), read_only=False, serial=True,
             argument_schema={'team_id': {'type': 'string'}, 'accept': {'type': 'boolean'}}, required_args=('team_id', 'accept')),
        Tool("team_stop", "停止一个任务小组，取消在跑的成员。", {"team_id": "小组 ID", "reason": "可选原因"},
             guarded(stop), read_only=False, serial=True, required_args=('team_id',), argument_schema={'team_id': {'type': 'string'}}),
    ]
