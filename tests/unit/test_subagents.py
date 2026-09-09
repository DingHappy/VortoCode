"""自定义子 agent（.vortocode/agents/*.md，公司架构式分工）单测——离线、假 LLM。

安全面是重点：dev 型角色只拿隔离流水线工具（绝无裸写/shell/PR）、永不递归（无 task）、
task 工具的 dev 委派过确认门（fail-closed）、坏定义文件安全跳过。
"""

import pytest

from src.agents.capabilities import LOCAL_PROFILE, SessionCapabilities
from src.agents.main_agent import build_subagent
from src.agents.subagents import _parse_agent_md, registry_for, subagent_catalog

_PM = """---
name: product-manager
description: 产品经理：拆需求
tools: read
---
你是产品经理。
"""

_BACKEND = """---
name: backend-dev
description: 后端开发
tools: dev
model: mimo-v2.5
max_steps: 8
---
你是后端开发工程师。
"""


def _write_agent(tmp_path, fname, text):
    d = tmp_path / ".vortocode" / "agents"
    d.mkdir(parents=True, exist_ok=True)
    (d / fname).write_text(text, encoding="utf-8")


# ------------------------------------------------------------ 解析与注册表
def test_parse_full_and_defaults(tmp_path):
    p = tmp_path / "pm.md"
    p.write_text(_PM, encoding="utf-8")
    s = _parse_agent_md(p)
    assert s.name == "product-manager" and s.tools == "read" and "产品经理" in s.system_prompt
    p2 = tmp_path / "bare.md"
    p2.write_text("你是极简角色。", encoding="utf-8")   # 无 frontmatter：文件名即角色名，默认只读
    s2 = _parse_agent_md(p2)
    assert s2.name == "bare" and s2.tools == "read" and s2.max_steps == 12


def test_parse_bad_inputs_safe(tmp_path):
    (tmp_path / "bad.md").write_text("---\ntools: [unclosed\n---\nx", encoding="utf-8")
    assert _parse_agent_md(tmp_path / "bad.md") is None          # 坏 YAML → 跳过
    (tmp_path / "empty.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    assert _parse_agent_md(tmp_path / "empty.md") is None        # 没正文（无 system prompt）→ 跳过
    (tmp_path / "weird.md").write_text("---\nname: w\ntools: root\n---\n提示词", encoding="utf-8")
    assert _parse_agent_md(tmp_path / "weird.md").tools == "read"  # 未知工具面收敛最小权限


def test_registry_load_and_catalog(tmp_path):
    _write_agent(tmp_path, "pm.md", _PM)
    _write_agent(tmp_path, "backend.md", _BACKEND)
    _write_agent(tmp_path, "broken.md", "---\n: [\n---\nx")      # 坏文件不拖垮
    reg = registry_for(str(tmp_path))
    assert set(reg.specs) == {"product-manager", "backend-dev"}
    cat = subagent_catalog(str(tmp_path))
    assert "product-manager" in cat and "只读" in cat
    assert "backend-dev" in cat and "写代码" in cat
    assert subagent_catalog(str(tmp_path / "nowhere")) == ""     # 无目录 → 空串不炸


# ------------------------------------------------------------ 装配安全面
def test_build_subagent_read_face(tmp_path):
    _write_agent(tmp_path, "pm.md", _PM)
    spec = registry_for(str(tmp_path)).get("product-manager")
    sub = build_subagent(
        str(tmp_path), spec, capabilities=SessionCapabilities.for_profile(LOCAL_PROFILE)
    )
    assert "read_file" in sub.tools
    assert not any(n.startswith("dev_") for n in sub.tools)      # read 型无 dev
    assert "task" not in sub.tools and "run_command" not in sub.tools   # 永不递归/无 shell
    assert "你是产品经理" in sub._system("plan")                  # 角色 system prompt 生效


def test_build_subagent_dev_face_is_isolated_pipeline_only(tmp_path):
    _write_agent(tmp_path, "backend.md", _BACKEND)
    spec = registry_for(str(tmp_path)).get("backend-dev")
    sub = build_subagent(str(tmp_path), spec)
    assert {"dev_isolated", "dev_parallel"} <= set(sub.tools)    # 有隔离流水线
    for banned in ("dev_auto", "open_pr", "run_command", "edit_file", "write_file", "task"):
        assert banned not in sub.tools, f"dev 型角色不该有 {banned}"
    assert sub.max_steps == 8                                    # max_steps 生效


def test_build_subagent_inherits_project_permissions(tmp_path):
    """项目级 permissions.yaml deny 必须继承到子 agent（#148 评审：否则角色文件成了
    绕过项目规则的后门——deny: [dev_isolated] 时 dev 型子 agent 照跑）。"""
    _write_agent(tmp_path, "backend.md", _BACKEND)
    (tmp_path / ".vortocode" / "permissions.yaml").write_text(
        "deny:\n  - dev_isolated\n", encoding="utf-8")
    spec = registry_for(str(tmp_path)).get("backend-dev")
    sub = build_subagent(str(tmp_path), spec)
    assert sub._permissions is not None
    assert sub._permissions.denied("dev_isolated", {})           # deny 规则真进了子 agent
    assert not sub._permissions.denied("read_file", {})          # 没被 deny 的照常


@pytest.mark.asyncio
async def test_denied_tool_blocked_at_dispatch_in_subagent(tmp_path):
    """行为级：被 deny 的工具在子 agent 执行层被硬拦（不是只挂了个对象）。"""
    _write_agent(tmp_path, "backend.md", _BACKEND)
    (tmp_path / ".vortocode" / "permissions.yaml").write_text(
        "deny:\n  - dev_isolated\n", encoding="utf-8")
    spec = registry_for(str(tmp_path)).get("backend-dev")
    sub = build_subagent(
        str(tmp_path), spec, capabilities=SessionCapabilities.for_profile(LOCAL_PROFILE)
    )
    out = await sub._run_tool("dev_isolated", {"description": "x"}, mode="build",
                              say=lambda _m: None)
    assert "权限拦截" in out                                      # 硬拦生效、最优先


# ------------------------------------------------------------ task 工具按名委派
class _EchoLLM:
    """记下子 agent 收到的 system，直接回一句结论。"""
    def __init__(self):
        self.systems: list = []

    async def chat(self, messages, **k):
        self.systems.append(next((m["content"] for m in messages if m["role"] == "system"), ""))
        return {"content": "结论：好了。"}


@pytest.mark.asyncio
async def test_task_dispatch_by_agent_name(tmp_path):
    from src.agents.main_agent import build_research_tools
    _write_agent(tmp_path, "pm.md", _PM)
    llm = _EchoLLM()
    tools = {t.name: t for t in build_research_tools(str(tmp_path), llm=llm)}
    out = await tools["task"].handler({"description": "拆一下这个需求", "agent": "product-manager"})
    assert "结论" in out
    assert any("你是产品经理" in s for s in llm.systems)          # 角色人格真进了子 agent


@pytest.mark.asyncio
async def test_task_unknown_agent_lists_available(tmp_path):
    from src.agents.main_agent import build_research_tools
    _write_agent(tmp_path, "pm.md", _PM)
    tools = {t.name: t for t in build_research_tools(str(tmp_path), llm=_EchoLLM())}
    out = await tools["task"].handler({"description": "x", "agent": "ceo"})
    assert "没有名为" in out and "product-manager" in out         # 报可用清单，不瞎跑


@pytest.mark.asyncio
async def test_task_dev_agent_gated_by_confirm(tmp_path):
    """dev 型委派过人闸：无确认通道 fail-closed 拒绝；拒绝应答不跑。"""
    from src.agents.main_agent import build_research_tools
    _write_agent(tmp_path, "backend.md", _BACKEND)
    llm = _EchoLLM()
    # 无 confirm → 拒绝
    tools = {t.name: t for t in build_research_tools(str(tmp_path), llm=llm)}
    out = await tools["task"].handler({"description": "实现 X", "agent": "backend-dev"})
    assert "拒绝" in out and llm.systems == []                    # 子 agent 根本没起
    # confirm 拒绝 → 取消
    async def _deny(_m):
        return False
    tools = {t.name: t for t in build_research_tools(str(tmp_path), llm=llm, confirm=_deny)}
    out = await tools["task"].handler({"description": "实现 X", "agent": "backend-dev"})
    assert "取消" in out and llm.systems == []
    # confirm 放行 → 真跑（假 LLM 直接收口）
    async def _allow(_m):
        return True
    tools = {t.name: t for t in build_research_tools(str(tmp_path), llm=llm, confirm=_allow)}
    out = await tools["task"].handler({"description": "实现 X", "agent": "backend-dev"})
    assert "结论" in out and any("后端开发" in s for s in llm.systems)


@pytest.mark.asyncio
async def test_default_task_unchanged_without_agent(tmp_path):
    """不带 agent 参数：默认只读研究员，行为与从前一致（向后兼容）。"""
    from src.agents.main_agent import build_research_tools
    llm = _EchoLLM()
    tools = {t.name: t for t in build_research_tools(str(tmp_path), llm=llm)}
    out = await tools["task"].handler({"description": "看看结构"})
    assert "结论" in out and any("只读研究子 agent" in s for s in llm.systems)


# ------------------------------------------------------------ 装配层目录注入 + 示例模板守门
def test_build_session_injects_agent_catalog(tmp_path, monkeypatch):
    _write_agent(tmp_path, "pm.md", _PM)
    from src.gateway.agent_session import build_session
    agent = build_session(str(tmp_path), kind="cli", confirm=None)
    sysmsg = agent._system("plan")
    assert "【可用子 agent】" in sysmsg and "product-manager" in sysmsg


def test_examples_parse_with_real_parser():
    """**每一个**角色示例都必须能被真解析器吃下（模板漂移即红，与 ops 模板守门同族）。

    刻意不硬编码模板个数——这条守的是"模板别写坏"，不是"永远只有这几个"；
    加一个新角色示例不该让它变红。四件套按名逐个断言，少了谁照样红。
    """
    from pathlib import Path
    d = Path(__file__).resolve().parents[2] / "examples" / "agents"
    files = sorted(d.glob("*.md.example"))
    specs = [_parse_agent_md(p) for p in files]
    broken = [f.name for f, s in zip(files, specs) if s is None]
    assert files and not broken, f"这些角色模板解析不动：{broken}"
    by_name = {s.name: s for s in specs}
    assert by_name["product-manager"].tools == "read" and by_name["qa"].tools == "read"
    assert by_name["backend-dev"].tools == "dev" and by_name["frontend-dev"].tools == "dev"
    assert by_name["scout"].tools == "read"        # 选材员只读：它不写文章、更不发布
