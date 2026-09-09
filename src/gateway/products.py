"""工序产出物（Product）——非代码流水线的交接载体，带血缘与污点传播。

**要解决的问题**：现在"部门"之间唯一的总线是主 agent 的上下文窗口。`task` 工具起一个子 agent、
跑一个回合、返回**一段自由文本**、随即消失（`main_agent.py` 的 `_spawn`）；除了 dev 型角色能落
`vorto/*` 分支，其余角色的成果没有任何持久形态。于是上下文一压缩、一换会话、一到 cron 的隔离
会话，交接就断——这不是提示词写得不好，是没有交接载体。

Product 就是那个载体：一道工序的产出落成一条带 id 的记录，下一道工序**按 id 取**，不靠上下文。

**与 `src/web/artifacts.py` 不是一回事**（那个是仿 Claude Code 的可分享 HTML 网页）。这里存的是
结构化的工序产出：选题池、内容包、发布回执、数据指标。两者刻意不共用存储与命名。

**血缘（inputs）不是装饰**，它同时承担三件事：
1. 交接——下一道工序声明自己消费了哪几条，读起来是确定的，不用猜；
2. 证据——"这篇文章是从哪条热点、哪份研究来的"可回溯，接得上 goals.py 的验收；
3. **污点传播**——见下。

**污点为什么必须沿血缘走**：`taint.py` 的污点是**回合级**的（ContextVar），回合一结束就没了。而
Product 跨回合、跨会话、跨天存在：一个 `topic_pool` 若来自 web_search，它就是外部不可信内容的
衍生物；三天后另一个会话把它读进来写文章，那个回合同样在处理不可信内容，却不会自动进入污点态
——免确认授权照样有效。这正是提示注入 D0 想堵的口子换了个时间维度重开。
所以：**任一 input 带污点 → 本 Product 带污点；消费带污点的 Product → 调用方须据此标污点回合。**
本模块只负责如实记录与传播这个事实，不替调用方决定怎么处置（那是确认门的事）。
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_DIRNAME = "products"
_BAD = re.compile(r"[^A-Za-z0-9_-]")
# payload 体积上限：产出物是给下一道工序读的结构化数据，不是文件仓库。
# 超限即拒绝而不是静默截断——截断过的选题池看起来一切正常，那是最坏的一种坏。
MAX_PAYLOAD_BYTES = 1024 * 1024


def _now() -> str:
    """**微秒**精度——本模块刻意不跟 goals/dev_plan 的秒级惯例。

    产出物的先后顺序是有语义的（`latest(kind)` 就是靠它喂给下一轮）。秒级精度下，同一秒内
    产出的两条时间戳相同，排序退化成按随机 id 排——`latest()` 于是随机给一条。goals/dev_plan
    没这个问题是因为它们的时间戳只用来显示，不用来判先后。
    `timespec="microseconds"` 而不是裸 `isoformat()`：后者在微秒恰为 0 时会省掉小数部分，
    宽度不一致的字符串排序是错的。
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _clean_id(value: str) -> Optional[str]:
    """清洗成文件名安全字符（挡 ../）；空/非法 → None。与 goals/dev_plan 同口径。"""
    if not value:
        return None
    cleaned = _BAD.sub("_", str(value)).strip("_")
    return cleaned or None


@dataclass
class Product:
    """一道工序的产出物。

    kind 是消费方唯一该依赖的契约（流水线定义里 `inputs: [kind]` 就是按它匹配）；
    payload 的具体形状由 kind + schema_version 约定。
    """

    id: str
    kind: str
    pipeline: str = ""                                   # 属于哪条流水线（如 content-ops）
    run_id: str = ""                                     # 哪一次运行
    stage: str = ""                                      # 哪道工序产出的
    inputs: List[str] = field(default_factory=list)      # 消费了哪些 product id（血缘）
    tainted: bool = False                                # 是否（直接或经血缘）含外部摄入内容
    taint_reason: str = ""                               # 为什么带污点——人要看得懂才有用
    schema_version: int = 1
    summary: str = ""                                    # 一行人读摘要（给日报/决策队列用）
    payload: Dict[str, Any] = field(default_factory=dict)
    created: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Product":
        known = {f for f in Product.__dataclass_fields__}          # 忽略未来新增字段，老文件照读
        kwargs = {k: v for k, v in (data or {}).items() if k in known}
        kwargs.setdefault("id", "")
        kwargs.setdefault("kind", "")
        return Product(**kwargs)


class ProductStore:
    """`.vortocode/products/<id>.json` 的存取。原子落盘，与 goals/dev_plan 同惯例。"""

    def __init__(self, repo_root: str) -> None:
        self.repo_root = str(repo_root)

    def _dir(self) -> Path:
        return Path(self.repo_root) / ".vortocode" / _DIRNAME

    def _path(self, product_id: str) -> Path:
        return self._dir() / f"{product_id}.json"

    # ---------------------------------------------------------------- 写
    def create(
        self,
        kind: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        summary: str = "",
        pipeline: str = "",
        run_id: str = "",
        stage: str = "",
        inputs: Optional[List[str]] = None,
        tainted: bool = False,
        taint_reason: str = "",
        schema_version: int = 1,
    ) -> Product:
        """产出一条 Product。**污点按血缘自动继承**，调用方给的 tainted 只能加不能减。

        输入里任一条带污点，本条就带污点——因为它的内容是从那条派生出来的。想"洗白"必须由人
        在确认门那一侧决定，不能靠某道工序自己声明干净（否则污点模型形同虚设）。
        """
        clean_kind = str(kind or "").strip()
        if not clean_kind:
            raise ValueError("Product 必须有 kind")
        body = dict(payload or {})
        size = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        if size > MAX_PAYLOAD_BYTES:
            raise ValueError(f"payload {size} 字节超过上限 {MAX_PAYLOAD_BYTES}——请拆分或改存引用")

        input_ids = [i for i in (str(x).strip() for x in (inputs or [])) if i]
        inherited, reasons = self._inherited_taint(input_ids)
        if taint_reason:
            reasons.insert(0, str(taint_reason).strip())

        product = Product(
            id=f"prod-{_clean_id(clean_kind) or 'x'}-{uuid.uuid4().hex[:10]}",
            kind=clean_kind,
            pipeline=str(pipeline or "").strip(),
            run_id=str(run_id or "").strip(),
            stage=str(stage or "").strip(),
            inputs=input_ids,
            tainted=bool(tainted) or inherited,
            taint_reason="；".join(dict.fromkeys(r for r in reasons if r)),
            schema_version=int(schema_version),
            summary=str(summary or "").strip(),
            payload=body,
            created=_now(),
        )
        if not self.save(product):
            raise OSError(f"产出物落盘失败：{product.id}")
        return product

    def save(self, product: Product) -> bool:
        pid = _clean_id(product.id)
        if pid is None:
            return False
        product.id = pid
        path = self._path(pid)
        try:
            from src.agents.dev_plan import ensure_state_gitignore

            ensure_state_gitignore(self.repo_root)     # 先保证 .vortocode/ 自忽略，别污染目标仓库
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(product.to_dict(), ensure_ascii=False, indent=2),
                            encoding="utf-8")
            temp.replace(path)
            return True
        except (OSError, TypeError, ValueError):
            return False

    # ---------------------------------------------------------------- 读
    def load(self, product_id: str) -> Optional[Product]:
        pid = _clean_id(product_id)
        if pid is None:
            return None
        path = self._path(pid)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Product.from_dict(data) if isinstance(data, dict) else None
        except (OSError, TypeError, ValueError):
            return None

    def list(
        self,
        *,
        kind: str = "",
        pipeline: str = "",
        run_id: str = "",
        limit: int = 50,
    ) -> List[Product]:
        """按条件列出，**新→旧**。坏文件跳过而不是让整个列表挂掉。"""
        directory = self._dir()
        if not directory.is_dir():
            return []
        found: List[Product] = []
        for path in directory.glob("*.json"):
            product = self.load(path.stem)
            if product is None:
                continue
            if kind and product.kind != kind:
                continue
            if pipeline and product.pipeline != pipeline:
                continue
            if run_id and product.run_id != run_id:
                continue
            found.append(product)
        found.sort(key=lambda p: (p.created, p.id), reverse=True)
        return found[:limit] if limit and limit > 0 else found

    def latest(self, kind: str, *, pipeline: str = "", run_id: str = "") -> Optional[Product]:
        """某个 kind 的最新一条——`scout` 读上一轮 metrics 就靠它。"""
        items = self.list(kind=kind, pipeline=pipeline, run_id=run_id, limit=1)
        return items[0] if items else None

    # ---------------------------------------------------------------- 血缘
    def lineage(self, product_id: str, *, max_depth: int = 20) -> List[Product]:
        """回溯血缘：本条 + 全部祖先，按遇到顺序去重。

        深度封顶且记录已访问集——产出物之间理论上不该成环（工序有拓扑序），但存储是可手改的，
        一个手滑的自引用不该让日报进程转到天荒地老。
        """
        root = self.load(product_id)
        if root is None:
            return []
        out: List[Product] = []
        seen = {root.id}
        frontier = [(root, 0)]
        while frontier:
            product, depth = frontier.pop(0)
            out.append(product)
            if depth >= max_depth:
                continue
            for parent_id in product.inputs:
                if parent_id in seen:
                    continue
                seen.add(parent_id)
                parent = self.load(parent_id)
                if parent is not None:
                    frontier.append((parent, depth + 1))
        return out

    def _inherited_taint(self, input_ids: List[str]) -> tuple:
        """输入里有没有带污点的；返回 (是否污点, 人读理由列表)。

        读不到的输入**按带污点处理**（fail-closed）：血缘断了就无法证明来源干净，
        而"证明不了干净"在污点模型里只能当脏——这与 UNATTENDED_PROFILE 的取向一致。
        """
        tainted = False
        reasons: List[str] = []
        for pid in input_ids:
            parent = self.load(pid)
            if parent is None:
                tainted = True
                reasons.append(f"输入 {pid} 读不到，无法确认来源")
                continue
            if parent.tainted:
                tainted = True
                reasons.append(parent.taint_reason or f"继承自 {pid}")
        return tainted, reasons
