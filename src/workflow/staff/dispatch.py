"""Compatibility task cards refer to the existing staff collaboration task, never a second execution."""
from copy import deepcopy

from src.workflow.staff.projects import check
from src.workflow.staff.registry import StaffRegistry
from src.workflow.tasks.tasks import TaskLedger


def binding(root, project):
    registry = StaffRegistry(root)
    choices = []
    for member in registry.members():
        role = registry.role(member['role'])
        if member['status'] != 'active' or not role['enabled'] or role.get('project') != project:
            continue
        registry.validate_member(member)
        check(role)
        choices.append({'member': member, 'role': role})
    if len(choices) != 1:
        raise ValueError('项目需要一个明确的已批准开发专员；没有时先登记，有多个时在主对话用 staff_assign 指定专员')
    return deepcopy(choices[0])


def send(root, entry):
    from src.scheduling.staff_runner import get_staff_service
    service = get_staff_service(root)
    frozen = entry['staff_binding']
    task_id = entry['remote_task_id']  # Existing stable submission identity also survives a lost receipt.
    owner = entry.get('staff_owner') or entry.get('session') or entry['remote_session']
    args = {'member': frozen['member']['id'], 'prompt': entry['goal'], 'acceptance': entry['criteria']}
    service.executable(frozen['role'])
    if service.assignment(args) != (frozen['member'], frozen['role']):
        raise ValueError('提议后的专员或岗位已变化，请重新提议；旧批准不迁移')
    existing = TaskLedger(root).load(task_id)
    if existing is not None:
        # A stored task is evidence of a submission, never permission to re-enqueue it.
        task = service.collaboration(owner).get(task_id)
        dispatch = task.collaboration.get('dispatch', {})
        if (task.prompt != args['prompt'] or task.collaboration['acceptance'] != args['acceptance']
                or dispatch.get('source') != 'staff' or dispatch.get('member') != frozen['member']['id']
                or dispatch.get('role_revision') != frozen['role']['revision']
                or dispatch.get('member_revision') != frozen['member']['revision']):
            raise ValueError('已有任务与批准内容不一致，未重复执行')
        return task
    return service.assign(owner, args, expected=(frozen['member'], frozen['role']), task_id=task_id)


def snapshot(root, entry):
    from src.workflow.tasks.collaboration import CollaborationService
    owner = entry.get('staff_owner') or entry.get('session') or entry['remote_session']
    task = CollaborationService(root, owner).get(entry['staff_task_id'])
    delivery = task.collaboration.get('delivery') or {}
    statuses = {'queued': 'running', 'running': 'running', 'blocked': 'waiting', 'waiting': 'waiting',
                'done': 'done', 'failed': 'failed', 'interrupted': 'stopped', 'cancelled': 'cancelled'}
    return {'status': statuses.get(task.status, 'stopped'), 'result': task.result, 'error': task.error,
            'branch': delivery.get('branch', ''), 'staff_review': task.collaboration.get('review'),
            'staff_delivery': delivery}


def publish(root, entry):
    """Owner's separate outward-action button; specialist execution never calls this."""
    import subprocess
    from src.agents.vcs import push_and_open_pr
    project = check(entry['staff_binding']['role'])
    current = snapshot(root, entry)
    delivery = current['staff_delivery']
    branch, head = delivery.get('branch'), delivery.get('head')
    if current['status'] != 'done' or current['error'] or not branch or not head:
        raise ValueError('开发专员没有可核对的完整交付，未推送')
    if branch != entry.get('branch') or (entry.get('staff_delivery') or {}).get('head') != head:
        raise ValueError('交付已变化，请刷新核对后重新确认外发')
    result = subprocess.run(['git', '-C', project['repo_root'], 'rev-parse', '--verify', f'refs/heads/{branch}'],
                            check=True, capture_output=True, text=True)
    if result.stdout.strip() != head:
        raise ValueError('交付分支已变化，请重新审查后再确认外发')
    return push_and_open_pr(project['repo_root'], branch, entry['title'],
        '主人已另行确认外发。开发专员交付的本地分支；验证记录请核对任务卡。', draft=True)
