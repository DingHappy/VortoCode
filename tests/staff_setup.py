"""Offline owner-receipt adapter for tests exercising existing dispatch clients."""
from copy import deepcopy
import json
import subprocess


def registered_developer(root, monkeypatch, name):
    from src.workflow.staff.approvals import StaffApprovals
    root.mkdir(parents=True, exist_ok=True)
    project = root / 'fixture-project'
    project.mkdir(exist_ok=True)
    (project / 'README.md').write_text('fixture\n')
    def git(*args):
        subprocess.run(['git', '-C', str(project), *args], check=True, capture_output=True)
    git('init', '-q', '-b', 'main')
    git('add', '.')
    git('-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
    path = root / 'resident_projects.json'
    path.write_text(json.dumps({'projects': [{'name': name, 'repo_root': str(project), 'port': 8082}]}))
    monkeypatch.setenv('VORTOONE_RESIDENT_PROJECTS', str(path))
    approvals = StaffApprovals(root)
    role = deepcopy(approvals.registry.role('developer'))
    for key in ('builtin', 'revision', 'enabled'):
        role.pop(key)
    role.update(id='fixture-developer', project=name)
    approvals.registry.register_role(approvals.snapshot('role', role)['data'])
    return approvals.registry.register_member({'id': 'fixture-dev', 'name': '开发专员',
                                               'role': role['id'], 'engine': 'opencode'})
