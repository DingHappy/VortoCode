"""Proposal preparation grants nothing; only explicit owner approval can register staff."""
import json

from src.agents.taint import is_tainted
from src.agents.tool import Tool, PreparedToolCall, ToolResult
from src.workflow.staff.approvals import StaffApprovals


def build_staff_proposal_tool(root, *, confirm, owner):
    approvals = StaffApprovals(root)
    async def prepare(args):
        if is_tainted():
            return ToolResult('污点回合不能提议岗位、专员或部门；请另开干净回合。', 'blocked')
        if not isinstance(args, dict) or set(args) != {'kind', 'data', 'reason'}:
            raise ValueError('提议只接受 kind、data 和 reason')
        snapshot = approvals.snapshot(args['kind'], args['data'])
        who = owner() if callable(owner) else owner
        async def apply():
            if is_tainted() or (owner() if callable(owner) else owner) != who:
                raise ValueError('回合或会话已变化，未登记')
            proposal = approvals.propose(who, args['kind'], args['data'], args['reason'], expected=snapshot)
            return json.dumps({'proposal': proposal, 'next_action':
                '到设置「团队与权限」核对完整内容，由主人确认后才登记。'}, ensure_ascii=False)
        message = ('登记提议，核对以下全部内容：\n' + json.dumps(snapshot, ensure_ascii=False)
                   + '\n理由：' + str(args['reason'])
                   + '\n这次只保留待批提议；尚不授予权限，仍需主人在 Desktop 核对后批准。')
        return PreparedToolCall(apply, message, 'execute')
    return Tool.prepared('staff_propose', '提议岗位、专员或部门；只保留待批记录，主人须在 Desktop 核对后批准。污点回合禁止。',
        {'kind': 'role、member 或 department', 'data': '完整登记对象；开发岗位复制内置 developer 的隔离权限，另设 id 与 project=已登记项目名，目标由宿主锁定，须主人确认；预置常设岗位首次安装用 {id, install:true}；旧内置岗位升级用 {id, revision, upgrade:true}，须主人批准；department 不附带权限或笔记', 'reason': '为什么需要登记或调整'},
        prepare, operation_kind='execute', always_confirm=True, serial=True, confirm=confirm,
        required_args=('kind', 'data', 'reason'), argument_schema={'kind': {'type': 'string', 'enum': ['role', 'member', 'department']},
        'data': {'type': 'object'}, 'reason': {'type': 'string', 'minLength': 1, 'maxLength': 2000}})


def build_routine_proposal_tool(root, *, confirm, owner):
    base = build_staff_proposal_tool(root, confirm=confirm, owner=owner)
    async def prepare(args):
        if set(args) != {'data', 'reason'}:
            raise ValueError('例行工作提议只接受 data 和 reason')
        return await base.prepare({'kind': 'routine', **args})
    return Tool.prepared('routine_propose',
        '提议新增或修改例行工作：到点交给已登记专员，不能附带权限、模型或框架；所有档位都须主人批准，污点回合禁止。',
        {'data': 'id、name、member、prompt、schedule（at/every/五段式/once 带偏移时间）、timezone；修改带 revision，默认交付 review',
         'reason': '设为例行工作的理由'}, prepare, operation_kind='execute', always_confirm=True,
        serial=True, confirm=confirm, required_args=('data', 'reason'),
        argument_schema={'data': {'type': 'object'}, 'reason': {'type': 'string', 'minLength': 1, 'maxLength': 2000}})
