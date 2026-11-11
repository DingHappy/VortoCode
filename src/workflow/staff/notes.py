"""Versioned department notes and pending suggestions; only explicit owner decisions write notes."""
from copy import deepcopy
import json
import uuid

from src.workflow.staff.approvals import digest
from src.workflow.staff.registry import StaffRegistry
from src.workflow.tasks.tasks import TASK_STATE_LOCK

MAX_NOTES = 16000


class StaffNotes:
    def __init__(self, root):
        self.registry = StaffRegistry(str(root))

    def _name(self, department):
        from src.workflow.staff.registry import _ID
        if not isinstance(department, str) or not _ID.fullmatch(department) or not department.startswith('dept-'):
            raise ValueError('部门 id 无效')
        return 'notes-' + department + '.json'

    def _read(self, department):
        state = self.registry._read(self._name(department), {'version': 1,
            'current': {'revision': 0, 'text': '', 'sources': [], 'tainted': False}, 'pending': [], 'history': [], 'applying': None})
        if not isinstance(state.get('current'), dict) or not isinstance(state.get('pending'), list) or not isinstance(state.get('history'), list):
            raise ValueError('部门笔记记录损坏')
        for item in state['pending']:
            if not isinstance(item, dict) or item.get('digest') != digest({k: v for k, v in item.items() if k != 'digest'}):
                raise ValueError('待确认笔记摘要不一致')
        return state

    def snapshot(self, department):
        with TASK_STATE_LOCK:
            state = self._read(department)
            if state.get('applying'):
                raise ValueError('部门笔记保存中断，请主人核对并重试原决定')
            current = deepcopy(state['current'])
            if (set(current) != {'revision', 'text', 'sources', 'tainted'} or type(current.get('revision')) is not int
                    or current['revision'] < 0 or type(current.get('tainted')) is not bool
                    or not isinstance(current.get('sources'), list) or any(not isinstance(x, str) for x in current['sources'])
                    or not isinstance(current.get('text'), str) or len(current['text']) > MAX_NOTES):
                raise ValueError('部门笔记损坏或超过长度上限')
            if current['revision']:
                path = self.registry.directory / 'notes' / (department + '.md')
                if path.is_symlink() or path.parent.is_symlink() or path.stat().st_size > MAX_NOTES * 4 or path.read_text(encoding='utf-8') != current['text']:
                    raise ValueError('部门笔记与已批准版本不一致')
            return {**current, 'digest': digest(current)}

    def pending(self, department):
        return deepcopy(self._read(department)['pending'])

    def propose(self, department, entries, *, task_id, round, member, sources):
        if not isinstance(entries, list) or len(entries) > 8:
            raise ValueError('每轮最多 8 条笔记建议')
        self.registry.department(department)
        with TASK_STATE_LOCK:
            state = self._read(department)
            for index, text in enumerate(entries):
                if not isinstance(text, str) or not text.strip() or len(text) > 2000:
                    raise ValueError('笔记建议须为 1～2000 字符')
                item = {'id': f'{task_id}:{round}:{index}', 'text': text, 'task_id': task_id,
                    'round': round, 'member': member, 'sources': sorted(set(sources)), 'tainted': True}
                item['digest'] = digest(item)
                existing = next((x for x in [*state['pending'], *state['history']] if x.get('id') == item['id']), None)
                if existing:
                    if existing['digest'] != item['digest']:
                        raise ValueError('同一轮的笔记建议已存在且内容不同')
                    continue
                if len(state['pending']) >= 64:
                    raise ValueError('待确认笔记已达上限，先由主人处理')
                state['pending'].append(item)
            self.registry._write(self._name(department), state)

    def _markdown(self, department, text):
        directory = self.registry.directory / 'notes'
        if directory.is_symlink():
            raise ValueError('部门笔记目录不能是符号链接')
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (department + '.md')
        if path.is_symlink():
            raise ValueError('部门笔记文件不能是符号链接')
        temporary = directory / ('.' + department + '-' + uuid.uuid4().hex)
        try:
            temporary.write_text(text, encoding='utf-8'); temporary.chmod(0o600); temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def decide(self, department, selected, expected_revision, *, owner_text='', public=False):
        """selected maps suggestion ids to {digest, approve}; only owner HTTP/UI calls this."""
        from src.workflow.staff.departments import Departments
        if not isinstance(selected, dict) or len(selected) > 64 or not selected and not owner_text:
            raise ValueError('选择待确认条目或输入主人原话')
        if not isinstance(owner_text, str) or len(owner_text) > 2000 or type(public) is not bool:
            raise ValueError('主人笔记内容无效')
        with TASK_STATE_LOCK:
            assigned = self.registry.department(department)
            state = self._read(department)
            request = digest({'selected': selected, 'revision': expected_revision, 'owner_text': owner_text, 'public': public})
            if state['history'] and state['history'][-1].get('request') == request:
                return self.snapshot(department)
            applying = state.get('applying')
            if applying and applying['request'] != request:
                raise ValueError('已有批准待完成，请先重试原决定')
            if not applying:
                if type(expected_revision) is not int or state['current']['revision'] != expected_revision:
                    raise ValueError('笔记版本已变化')
                current = self.snapshot(department)
                accepted, sources, tainted = [], set(current['sources']), current['tainted']
                for item_id, choice in selected.items():
                    item = next((x for x in state['pending'] if x['id'] == item_id), None)
                    if (not item or not isinstance(choice, dict) or set(choice) != {'digest', 'approve'}
                            or choice['digest'] != item['digest'] or type(choice['approve']) is not bool):
                        raise ValueError('笔记建议已变化或选择无效')
                    if choice['approve']:
                        accepted.append(item['text']); sources.update(item['sources']); tainted = tainted or item['tainted']
                private = Departments(self.registry).check_notes(assigned, {'sources': sorted(sources), 'tainted': tainted})
                if owner_text:
                    accepted.append(owner_text)
                if accepted and not private and not public:
                    raise ValueError('公开部门的笔记会供联网岗位读取，须明确按公开内容保存')
                text = current['text'] + ''.join('\n- ' + value + '\n' for value in accepted)
                if len(text) > MAX_NOTES:
                    raise ValueError('笔记超过全文上限，未静默截断或保存')
                target = {'revision': current['revision'] + int(bool(accepted)), 'text': text,
                          'sources': sorted(sources), 'tainted': tainted}
                state['applying'] = {'request': request, 'target': target, 'selected': selected}
                self.registry._write(self._name(department), state)  # Owner receipt precedes Markdown.
            return self._complete(department, assigned, state)

    def _complete(self, department, assigned, state):
        from src.workflow.staff.departments import Departments
        commit = state['applying']
        Departments(self.registry).check_notes(assigned, commit['target'])
        if commit['target']['revision']:
            self._markdown(department, commit['target']['text'])
        handled = [x for x in state['pending'] if x['id'] in commit['selected']]
        state['pending'] = [x for x in state['pending'] if x['id'] not in commit['selected']]
        state['history'] = [*state['history'], *({'id': x['id'], 'digest': x['digest'],
            'approve': commit['selected'][x['id']]['approve']} for x in handled), {'request': commit['request']}][-256:]
        state['current'], state['applying'] = commit['target'], None
        self.registry._write(self._name(department), state)
        return self.snapshot(department)

    def resume(self, department, request):
        with TASK_STATE_LOCK:
            state = self._read(department)
            if not state.get('applying'):
                if state['history'] and state['history'][-1].get('request') == request:
                    return self.snapshot(department)
                raise ValueError('没有匹配的已批准保存')
            if state['applying']['request'] != request:
                raise ValueError('保存决定摘要不匹配')
            return self._complete(department, self.registry.department(department), state)

    def inspect(self, department):
        state = deepcopy(self._read(department))
        try:
            state['snapshot'] = self.snapshot(department)
            state['error'] = ''
        except (OSError, ValueError):
            state['snapshot'] = None
            state['error'] = '笔记版本不可执行；如有已批准的保存，请核对后完成原决定'
        return state


def split_staff_result(result):
    """Optional structured final answer only proposes untrusted data, never writes a note."""
    text = str(result or '')
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return text, []
    if isinstance(value, dict) and set(value) == {'result', 'note_suggestions'} and isinstance(value['result'], str):
        return value['result'], value['note_suggestions']
    return text, []
