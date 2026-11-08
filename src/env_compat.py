"""环境变量改名兼容。

两次改名：
- 2026-07（A3）：`AUTODEV_*` → `VORTOCODE_*`。少数几处用 :func:`env_compat` 逐个回落。
- 2026-11-08（ADR-018 批次 B）：`VORTOCODE_*` → `VORTOONE_*`，随产品改名 VortoOne。
  变量有两百来个，逐处回落既容易漏、也会让「清掉某个开关」的地方只清新名字、漏掉旧名字。
  所以改成**进程起来就统一换名**：:func:`adopt_legacy_env` 把环境里的 `VORTOCODE_X` 改成
  `VORTOONE_X` 并删掉旧名字（两个都设了以新名字为准），代码里只认新名字。
  `src/__init__.py` 导入时跑一次；`.env` 加载得更晚，加载后由 :func:`load_env_file` 再跑一次。
"""
import os
import warnings
from pathlib import Path
from typing import MutableMapping, Optional, Union

NEW_PREFIX = "VORTOONE_"
LEGACY_PREFIX = "VORTOCODE_"

_warned: set = set()
_legacy_seen: set = set()


def env_compat(new_key: str, old_key: str, default: Optional[str] = None) -> Optional[str]:
    """读环境变量：优先 new_key；缺省回落 old_key（首次回落打一次 DeprecationWarning）；都无则 default。"""
    v = os.getenv(new_key)
    if v is not None:
        return v
    old = os.getenv(old_key)
    if old is not None:
        if old_key not in _warned:
            _warned.add(old_key)
            warnings.warn(f"环境变量 {old_key} 已弃用，请改用 {new_key}（兼容回落生效中，将来会移除）",
                          DeprecationWarning, stacklevel=2)
        return old
    return default


def adopt_legacy_env(environ: Optional[MutableMapping[str, str]] = None) -> list:
    """把旧名字 `VORTOCODE_*` 换成 `VORTOONE_*`，返回这次换掉的旧名字。

    - 只有旧名字：值挪到新名字下。
    - 两个都有：以新名字为准，旧名字丢掉。
    - 旧名字一律从环境里删掉，子进程只会看到新名字；清掉某个开关时也就不会漏掉旧名字。
    """
    env = os.environ if environ is None else environ
    moved = sorted(key for key in env if key.startswith(LEGACY_PREFIX))
    for key in moved:
        value = env.pop(key)
        env.setdefault(NEW_PREFIX + key[len(LEGACY_PREFIX):], value)
    if environ is None:
        _legacy_seen.update(moved)
    return moved


def legacy_env_names() -> list:
    """本进程里出现过的旧名字（供 `vc doctor` 提醒改名）。"""
    return sorted(_legacy_seen)


def load_env_file(path: Union[str, Path]) -> bool:
    """加载 `.env`（不覆盖已有变量），然后把里面的旧名字换成新名字。没装 python-dotenv 返回 False。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    if Path(path).exists():
        load_dotenv(path)
        adopt_legacy_env()
    return True
