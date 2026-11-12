"""Owner-approved project authority belongs to a role, never to assignment arguments."""
from copy import deepcopy
from pathlib import Path
import subprocess


def target(name):
    from src.workflow.workbench.resident_projects import load_resident_projects
    matches = [row for row in load_resident_projects() if row['name'] == name]
    if len(matches) != 1:
        raise ValueError('项目未登记或登记有歧义，请重新核对项目')
    row = deepcopy(matches[0])
    try:
        row['repo_root'] = str(Path(row['repo_root']).resolve(strict=True))
        result = subprocess.run(['git', '-C', row['repo_root'], 'rev-parse', '--show-toplevel'],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError('登记项目暂不可核对，未执行') from error
    if result.returncode or str(Path(result.stdout.strip()).resolve()) != row['repo_root']:
        raise ValueError('登记项目必须指向 Git 仓库根目录')
    return row


def bind(data):
    item = deepcopy(data)
    if 'project' in item:
        current = target(item['project'])
        if 'project_binding' in item and item['project_binding'] != current:
            raise ValueError('登记项目已变化，须重新提议并批准岗位')
        item['project_binding'] = current
    return item


def check(role):
    binding = role.get('project_binding')
    if not binding or target(role.get('project')) != binding:
        raise ValueError('岗位没有有效的固定项目或登记项目已变化，须重新批准')
    return deepcopy(binding)


def validate(role):
    if not ({'project', 'project_binding'} & set(role)):
        return  # Existing unbound roles remain readable, but cannot execute alone.
    from src.workflow.staff.roles import builtin_roles
    developer = next(item for item in builtin_roles() if item['id'] == 'developer')
    binding = role.get('project_binding')
    if (not isinstance(role.get('project'), str) or not role['project']
            or not isinstance(binding, dict) or set(binding) not in (
                {'name', 'repo_root', 'port'}, {'name', 'repo_root', 'port', 'instance_id'})
            or binding.get('name') != role['project'] or not isinstance(binding.get('repo_root'), str)
            or not Path(binding['repo_root']).is_absolute()
            or type(binding.get('port')) is not int or not 1024 <= binding['port'] <= 65535):
        raise ValueError('岗位固定项目绑定无效')
    if 'instance_id' in binding:
        import re
        if not isinstance(binding['instance_id'], str) or not re.fullmatch(r'[a-f0-9]{32}', binding['instance_id']):
            raise ValueError('岗位固定项目身份无效')
    # ACP permissions are the existing fixed developer contract, not arbitrary role tool grants.
    if role['grants'] != developer['grants'] or role['engine'] != developer['engine']:
        raise ValueError('固定项目开发岗只能使用既有隔离开发权限；不能扩大或虚报权限')


def propose_development(root, owner, *, kind, id, name, project='', role=''):
    """Desktop owner form can request the fixed contract, never supply permission fields."""
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.roles import builtin_roles
    approvals = StaffApprovals(root)
    if kind == 'role' and project and not role:
        data = deepcopy(next(item for item in builtin_roles() if item['id'] == 'developer'))
        for field in ('builtin', 'enabled', 'revision'):
            data.pop(field)
        data.update(id=id, title=name, project=project)
        previous = next((item for item in approvals.registry.roles() if item['id'] == id), None)
        if previous:
            data['revision'] = previous['revision']
        return approvals.propose(owner, 'role', data, '主人在 Desktop 提议固定项目的隔离开发岗位')
    if kind == 'member' and role and not project:
        assigned = approvals.registry.role(role)
        check(assigned)
        return approvals.propose(owner, 'member', {'id': id, 'name': name, 'role': role, 'engine': 'opencode'},
                                 '主人在 Desktop 提议固定项目的开发专员')
    raise ValueError('岗位须选择登记项目；专员须选择已批准的固定项目岗位')
