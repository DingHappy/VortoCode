"""主 agent 管任务小组的工具（ADR-014 S1d）：开小组、看进度、回话、验收、加预算、停止。

- 开小组与加预算会启动成员、花模型额度：按「执行」类过确认门，卡片列出目标、验收、成员、档位与额度。
- 验收与回话是主人自己的事项（OWN_DATA）：污点回合里照样要问。
- 停止是保护性动作，不问。
- 看进度只给代码拼的结构化视图；协调人写的总结在根带污点时按外部内容给（本回合即为污点）。
- 只有本进程装配了任务调度器（常驻助理）时才挂这些工具。
"""
from __future__ import annotations

import json
from typing import Any, List

from src.agents.tool import Tool

_ROLE_TEXT = {"web-researcher": "联网查资料", "reviewer": "只看交给它的材料"}


def _card(objective: str, acceptance: List[str], members: List[dict], mode: str, limits: dict) -> str:
    lines = ["开一个任务小组？", f"目标：{objective}", "验收标准："]
    lines += [f"  {index + 1}. {text}" for index, text in enumerate(acceptance)]
    lines.append("成员：" + "、".join(f"{item.get('name')}（{_ROLE_TEXT.get(item.get('role'), item.get('role'))}"
                                      + (f"，模型 {item['model']}" if item.get("model") else "") + "）" for item in members))
    lines.append("档位：" + ("open（成员可联网，小组看不到你的记忆、目标和对话）" if mode == "open"
                            else "private（成员不能联网）"))
    lines.append(f"额度：最多 {limits['tasks']} 个子任务、{limits['rounds']} 轮、累计 {limits['timeout_seconds'] // 60} 分钟、"
                 f"协调人醒来 {limits['wakes']} 次；同时最多 {limits['parallel']} 个成员在跑")
    return "\n".join(lines)


def build_team_tools(repo_root: str, *, confirm: Any, owner: str) -> List[Tool]:
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
        card = _card(objective, acceptance, members, mode, limits)
        if not await request(confirm, card, EXECUTE):
            return "主人没有同意开这个小组；不要重复提议同一内容，问清楚要怎么改。"
        root = service().create(owner, objective=objective, acceptance=acceptance, members=members, mode=mode,
                                limits=limits)
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

    async def add_budget(args: dict) -> str:
        team_id = str(args.get("team_id") or "")
        extra = {key: value for key, value in (args.get("extra") or {}).items() if type(value) is int}
        text = "、".join(f"{key} +{value}" for key, value in extra.items())
        if not extra or not await request(confirm, f"给任务小组 {team_id} 加预算：{text}？", EXECUTE):
            return "没有加预算。"
        service().add_budget(team_id, extra)
        runner().wake(team_id, "主人加了预算，接着做")
        return f"已给小组 {team_id} 加预算：{text}。"

    async def stop(args: dict) -> str:
        root = runner().stop(str(args.get("team_id") or ""), str(args.get("reason") or "主人停止"))
        return f"已停止小组 {root.id}；在跑的成员已请求取消。"

    return [
        Tool("team_propose",
             "大任务（需要多个角色、多轮，例如先查资料再审查、比较方案）时提议开一个任务小组，由主人确认后开工。"
             "成员角色可选：web-researcher（联网查资料）、reviewer（审查材料）。小任务直接自己做，不要开小组。",
             {"objective": "目标", "acceptance": "验收标准列表（1–10 条）",
              "members": "成员列表，每项 {name, role, model 可选}", "mode": "open（默认）或 private",
              "limits": "可选额度 {tasks, rounds, steps, timeout_seconds, wakes, parallel}"},
             guarded(propose), read_only=False, serial=True,
             argument_schema={"acceptance": {"type": "array", "items": {"type": "string"}},
                              "members": {"type": "array", "items": {"type": "object", "properties": {
                                  "name": {"type": "string"}, "role": {"type": "string", "enum": ["web-researcher", "reviewer"]},
                                  "model": {"type": "string"}}}},
                              "limits": {"type": "object"}}),
        Tool("team_list", "列出进行中的任务小组：状态、成员、子任务、额度用量。", {}, guarded(overview), read_only=True),
        Tool("team_open", "查看一个任务小组的详情；交付了的会带上协调人的总结和逐条证据。",
             {"team_id": "小组 ID"}, guarded(open_one), read_only=True),
        Tool("team_reply", "把主人的回答转给正在等主人的任务小组。", {"team_id": "小组 ID", "text": "主人的原话"},
             guarded(reply), read_only=False, serial=True),
        Tool("team_decide", "主人对小组的交付做决定：accept=true 验收通过，false 退回并写明原因。",
             {"team_id": "小组 ID", "accept": "true/false", "note": "退回原因"}, guarded(decide), read_only=False, serial=True,
             argument_schema={"accept": {"type": "boolean"}}),
        Tool("team_add_budget", "主人同意后给小组加预算。extra 例如 {\"wakes\": 8, \"rounds\": 4}。",
             {"team_id": "小组 ID", "extra": "要加的额度"}, guarded(add_budget), read_only=False, serial=True,
             argument_schema={"extra": {"type": "object"}}),
        Tool("team_stop", "停止一个任务小组，取消在跑的成员。", {"team_id": "小组 ID", "reason": "可选原因"},
             guarded(stop), read_only=False, serial=True),
    ]
