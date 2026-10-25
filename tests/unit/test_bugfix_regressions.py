"""核心 bug 修复的回归测试（B2/B3/B5/B6）。"""


import pytest


@pytest.mark.parametrize('action', ['allow', 'ask'])
async def test_mcp_yaml_never_overrides_kernel_gate(mcp_permission_probe, monkeypatch, action):
    from src.agents import gate, taint
    asked = []
    async def would_approve(message): asked.append(message); return True
    confirm = gate.make_confirm_gate(would_approve, auto_approve=True, can_ask_human=False)
    manager, tools, client = await mcp_permission_probe(confirm, action)
    taint.reset_taint()
    try:
        assert await tools[0].handler({}) == 'probe executed'
        taint.mark_tainted()
        assert (await tools[0].handler({}))['ok'] is False and client.calls == 1 and not asked
        taint.reset_taint()
        monkeypatch.setattr(gate, 'decide', lambda **_: gate.DENY)
        assert (await tools[0].handler({}))['ok'] is False and client.calls == 1
    finally:
        taint.reset_taint(); await manager.shutdown()


async def test_mcp_allow_without_confirm_fails_closed(mcp_permission_probe):
    manager, tools, client = await mcp_permission_probe()
    try:
        assert (await tools[0].handler({}))['ok'] is False and client.calls == 0
    finally:
        await manager.shutdown()


@pytest.mark.parametrize('approved', [False, True])
async def test_tainted_mcp_allow_requires_real_answer(mcp_permission_probe, approved):
    from src.agents import gate, taint
    prompts = []
    async def human(message): prompts.append(message); return approved
    manager, tools, client = await mcp_permission_probe(gate.make_confirm_gate(human, auto_approve=True, can_ask_human=True))
    taint.mark_tainted()
    try:
        result = await tools[0].handler({})
        assert client.calls == int(approved) and len(prompts) == 1 and gate.TAINT_WARNING in prompts[0]
        assert (result == 'probe executed') is approved
    finally:
        taint.reset_taint(); await manager.shutdown()


def test_b2_hook_registry_load_no_importerror(tmp_path):
    """B2: load_from_config 曾从 .hook 误导入而必崩；现应安全。"""
    from src.hooks.registry import HookRegistry
    # 配置不存在时直接返回，且导入路径正确，不抛 ImportError
    HookRegistry().load_from_config(str(tmp_path / "nope.yaml"))


@pytest.mark.asyncio
async def test_b3_decomposition_is_chained():
    """B3: 子任务依赖应为链式（只指向上一个），而非依赖之前全部。"""
    from src.agents.task_analyzer import (
        TaskDecomposer, TaskAnalysis, TaskComplexity,
    )
    dec = TaskDecomposer(use_llm=False)   # 验证规则版链式分解
    analysis = TaskAnalysis(
        complexity=TaskComplexity.COMPLEX,
        required_capabilities=["architecture", "code_generation", "testing", "code_review"],
    )
    subs = await dec.decompose("build a thing", analysis)
    assert len(subs) == 4
    assert subs[0].dependencies == []
    for prev, cur in zip(subs, subs[1:]):
        assert cur.dependencies == [prev.id]   # 每个只依赖紧邻的上一个


# 注：test_b5_taskqueue_stop_does_not_hang 已随 src/core/task_queue（从未接线的骨架）
# 于 2026-07 退役删除。后台任务改由 src/workflow/tasks/tasks.TaskRunner 承担，其测试见 test_gateway_tasks.py。


# ── 无头 CLI 静默失败（2026-10-17 真机：本机没装 openai SDK）──────────────────────────────
# 原生工具调用报「需要 OpenAI SDK」被当成瞬时故障 → 不回退提示式、回合以空回复结束；
# 无头 CLI 又把唯一的死因（emit 出的提示）丢掉 → 用户看到「空输出 + 退出码 0」。

class _NativeFailingLLM:
    """带 tools 的原生请求抛给定异常；不带 tools（提示式协议）正常回复。"""

    def __init__(self, error, reply="OK"):
        self.error, self.reply, self.native_calls = error, reply, 0

    async def chat(self, messages, **kwargs):
        if kwargs.get("tools"):
            self.native_calls += 1
            raise self.error
        return {"content": self.reply}


async def test_missing_sdk_falls_back_to_prompt_protocol(monkeypatch, capsys):
    from src import cli
    from src.llm.client import NativeToolsUnavailable

    monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", "1")
    llm = _NativeFailingLLM(NativeToolsUnavailable("原生工具调用需要 OpenAI SDK"))
    assert await cli.run_agent_headless("你好", llm=llm) == "OK"
    assert llm.native_calls == 1                    # 一次即永久回退，不每步重撞
    assert capsys.readouterr().out.strip() == "OK"


@pytest.mark.parametrize("as_json", [False, True])
async def test_headless_reports_channel_failure_instead_of_silent_exit(monkeypatch, capsys, as_json):
    import json
    from src import cli

    monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", "1")
    with pytest.raises(SystemExit) as exit_info:
        await cli.run_agent_headless("你好", llm=_NativeFailingLLM(RuntimeError("Request timed out")),
                                     as_json=as_json, quiet=True)
    assert exit_info.value.code == 1
    cap = capsys.readouterr()
    assert "模型服务暂时无响应" in cap.err and "timed out" in cap.err   # --quiet 也要说清死因
    if as_json:
        assert "模型服务暂时无响应" in json.loads(cap.out)["error"]
    else:
        assert "模型服务暂时无响应" not in cap.out    # 死因不进 stdout，免得污染管道


async def test_tainted_mcp_confirm_shows_one_banner_and_audits_raw_operation(mcp_permission_probe):
    """会话确认门已经是内核 gate 时，MCP 执行层不能再包一层：否则污点横幅叠两次，
    内层审计拿到的 operation 也是带横幅的文本而不是原始操作。"""
    from src.agents import gate, taint
    prompts, decisions = [], []
    async def human(message): prompts.append(message); return True
    session_gate = gate.make_confirm_gate(human, can_ask_human=True,
                                          on_decision=lambda op, ok, t: decisions.append(op))
    manager, tools, client = await mcp_permission_probe(session_gate)
    taint.mark_tainted()
    try:
        assert await tools[0].handler({}) == 'probe executed'
        banner = gate.taint_prefix()
        assert len(prompts) == 1 and prompts[0].count(banner.strip()) == 1
        assert len(decisions) == 1 and banner.strip() not in decisions[0]
        assert decisions[0].startswith('MCP 工具 `')
    finally:
        taint.reset_taint(); await manager.shutdown()
