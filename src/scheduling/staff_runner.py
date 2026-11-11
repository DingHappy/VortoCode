"""Register the resident assistant's existing execution pool, without another scheduler."""
from pathlib import Path
from functools import partial

_SERVICES = {}


def set_staff_service(root, runner=None, *, on_update=None):
    key = str(Path(root).resolve())
    if runner is None:
        _SERVICES.pop(key, None)
        return None
    from src.agents.staff import execute_staff
    from src.workflow.staff.service import StaffService
    service = StaffService(key, runner, execute=None, on_update=on_update)
    service.execute = partial(execute_staff, key, approval_service=service)
    service.actions.recover()
    service.recover_quotas()
    from src.workflow.staff.routines import Routines
    Routines(key).recover()
    _SERVICES[key] = service
    return service


def get_staff_service(root):
    service = _SERVICES.get(str(Path(root).resolve()))
    if service is None:
        raise ValueError('专员派活需要常驻助理的现有后台执行池；当前入口尚未连接')
    return service
