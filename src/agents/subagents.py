"""自定义子 agent 注册表（`.vortocode/agents/*.md`）——公司架构式的角色分工。

一个文件一个角色（产品经理/前端/后端/QA…），frontmatter 定元数据、正文即该角色的
system prompt。主 agent 的 `task`/`research_parallel` 工具用 `agent` 参数按名委派：

    ---
    name: backend-dev
    description: 后端开发：负责 src/api 与数据层的实现
    tools: dev            # read（默认：只读研究/评审型）| dev（可用隔离 dev 流水线真写代码）
    model: mimo-v2.5      # 可选：该角色用哪个模型（缺省随主 agent）
    max_steps: 12         # 可选：单回合工具预算
    ---
    你是后端开发工程师，只负责 src/api 与数据层……

安全面（铁律不破）：
- `tools: read`（默认）：只读工具面，绝不改文件——产品经理/代码评审/QA 分析型角色。
- `tools: dev`：额外给 dev_isolated/dev_parallel——**写入只经隔离流水线落 vorto/* 分支**，
  绝不碰主工作区；push/开 PR 不在子 agent 工具面里（收口仍在主 agent 的确认门）。
- 子 agent 永远没有 `task`（不递归）、没有裸写工具/shell。
解析失败/字段缺失都安全跳过（坏文件不拖垮 agent）；与 SkillRegistry 同一套渐进披露：
只有 name+description 进主 agent 系统提示（catalog），正文按需加载。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_TOOL_FACES = ("read", "dev")


@dataclass
class SubagentSpec:
    name: str
    description: str
    system_prompt: str
    tools: str = "read"                    # read | dev
    model: Optional[str] = None
    max_steps: int = 12


def _parse_agent_md(path: Path) -> Optional[SubagentSpec]:
    """解析一个 agents/*.md：YAML frontmatter + 正文。不合法返回 None（安全跳过）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    body = text
    meta: dict = {}
    if text.lstrip().startswith("---"):
        try:
            import yaml
            head = text.lstrip()
            _, fm, body = head.split("---", 2)
            meta = yaml.safe_load(fm) or {}
            if not isinstance(meta, dict):
                return None
        except Exception:  # noqa: BLE001
            return None
    name = str(meta.get("name") or path.stem).strip()
    prompt = body.strip()
    if not name or not prompt:
        return None                        # 没角色名/没 system prompt 的定义没意义
    tools = str(meta.get("tools") or "read").strip().lower()
    if tools not in _TOOL_FACES:
        tools = "read"                     # 未知工具面收敛到最小权限（fail-closed）
    try:
        max_steps = max(1, min(int(meta.get("max_steps") or 12), 40))
    except (TypeError, ValueError):
        max_steps = 12
    return SubagentSpec(name=name, description=str(meta.get("description") or "").strip(),
                        system_prompt=prompt, tools=tools,
                        model=(str(meta["model"]).strip() if meta.get("model") else None),
                        max_steps=max_steps)


class SubagentRegistry:
    """扫描 `.vortocode/agents/*.md`；坏文件安全跳过。同名后扫的覆盖先扫的（目录序稳定）。"""

    def __init__(self, dirs: list) -> None:
        self._dirs = [Path(d) for d in dirs]
        self.specs: dict = {}

    def load(self) -> "SubagentRegistry":
        self.specs.clear()
        for d in self._dirs:
            if not d.is_dir():
                continue
            for p in sorted(d.glob("*.md")):
                spec = _parse_agent_md(p)
                if spec is not None:
                    self.specs[spec.name] = spec
        return self

    def catalog(self) -> str:
        """给主 agent 系统提示的角色清单（name+description+工具面）。无定义则空串。"""
        return "\n".join(
            f"- {s.name}：{s.description or '（无描述）'}"
            f"（{'可用隔离 dev 流水线写代码' if s.tools == 'dev' else '只读'}）"
            for s in self.specs.values())

    def get(self, name: str) -> Optional[SubagentSpec]:
        return self.specs.get((name or "").strip())


def registry_for(repo_root: str) -> SubagentRegistry:
    """每次现建现扫（文件少、开销小），保证新加/改过的角色文件立刻生效，无缓存失效问题。"""
    return SubagentRegistry([str(Path(repo_root) / ".vortocode" / "agents")]).load()


def subagent_catalog(repo_root: str) -> str:
    """角色目录串，供装配层注入系统提示——让模型知道有哪些角色可按名委派。"""
    try:
        return registry_for(repo_root).catalog()
    except Exception:  # noqa: BLE001
        return ""


_SUB_RULES = ("\n\n【子 agent 通用约束】你是被主 agent 委派的角色，只做角色职责内的事；"
              "完成后返回**简洁结论**（发现/建议/产出物指引），别复述过程。")
_DEV_RULES = ("你可以用 dev_isolated/dev_parallel 真正实现代码——它们在隔离 worktree 里做、"
              "自测绿才落 vorto/* 分支，绝不碰主工作区；除此之外你没有任何直接写文件的手段。")


def build_subagent(repo_root: str, spec: SubagentSpec, *, llm=None,
                   confirm=None, on_progress=None):
    """按角色定义装配一个子 agent（MainAgent）。

    工具面：read=只读；dev=只读 + dev_isolated/dev_parallel（隔离流水线，落 vorto/* 分支）。
    永远不给 task/research_parallel（不递归）、不给裸写/shell/PR 工具。
    """
    from src.agents.main_agent import MainAgent, build_dev_tools, build_read_tools
    from src.agents.permissions import load_permissions

    tools = build_read_tools(repo_root)
    extra = spec.system_prompt + _SUB_RULES
    if spec.tools == "dev":
        dev = [t for t in build_dev_tools(repo_root, on_progress=on_progress, confirm=confirm)
               if t.name in ("dev_isolated", "dev_parallel")]
        tools = tools + dev
        extra += _DEV_RULES
    # 项目级权限硬拦（.vortocode/permissions.yaml deny）**必须继承**——否则角色文件成了
    # 绕过项目规则的后门（deny: [dev_isolated] 时 dev 型子 agent 照跑，#148 评审）。
    sub = MainAgent(tools, llm=llm, max_steps=spec.max_steps, extra_system=extra,
                    permissions=load_permissions(repo_root))
    if spec.model:
        try:
            sub.set_model(spec.model)
        except Exception:  # noqa: BLE001 —— 模型名不合法就随主 agent，不拦委派
            pass
    return sub
