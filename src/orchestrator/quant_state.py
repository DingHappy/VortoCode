"""流水线状态持久化

将循环的迭代次数、上次执行时间等保存到磁盘，
重启后恢复，避免计数器归零。
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_PATH = ".vortocode/quant_state.json"


class PipelineState:
    """流水线持久化状态"""

    def __init__(self, path: str = _DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("Failed to load pipeline state: %s", e)
        return {"loops": {}, "last_save": None}

    def _save(self):
        self._data["last_save"] = datetime.now().isoformat()
        try:
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save pipeline state: %s", e)

    def get_loop_state(self, name: str) -> Dict[str, Any]:
        """获取某个循环的持久化状态"""
        return self._data.get("loops", {}).get(name, {})

    def save_loop_state(
        self,
        name: str,
        iterations: int = 0,
        last_check: Optional[str] = None,
        status: str = "idle",
    ):
        """保存某个循环的状态"""
        if "loops" not in self._data:
            self._data["loops"] = {}
        self._data["loops"][name] = {
            "iterations": iterations,
            "last_check": last_check,
            "status": status,
            "updated_at": datetime.now().isoformat(),
        }
        self._save()

    def get_total_iterations(self) -> int:
        """获取所有循环的总迭代次数"""
        return sum(
            loop.get("iterations", 0)
            for loop in self._data.get("loops", {}).values()
        )

    def get_all(self) -> Dict[str, Any]:
        return self._data.copy()

    def clear(self):
        """清除状态"""
        self._data = {"loops": {}, "last_save": None}
        self._save()
