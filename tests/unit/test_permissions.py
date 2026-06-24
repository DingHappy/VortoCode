"""细粒度工具权限（.vortocode/permissions.yaml deny 规则）—— src/agents/permissions.py
+ MainAgent._run_tool 硬拦。"""

import pytest

from src.agents.permissions import Permissions, load_permissions


def test_whole_tool_deny():
    perm = Permissions([("web_fetch", None)])
    assert "禁用" in perm.denied("web_fetch", {"url": "http://x"})
    assert perm.denied("read_file", {"path": "a"}) is None       # 其它工具不受影响


def test_arg_glob_deny_matches_primary_arg():
    perm = Permissions([("run_command", "rm *")])
    assert perm.denied("run_command", {"command": "rm -rf build"}) is not None
    assert perm.denied("run_command", {"command": "ls -la"}) is None    # 不匹配 → 放行
    # 主参数另一个别名 cmd 也认
    assert perm.denied("run_command", {"cmd": "rm x"}) is not None


def test_path_glob_deny():
    perm = Permissions([("edit_file", "*/secrets/*")])
    assert perm.denied("edit_file", {"path": "app/secrets/key.py"}) is not None
    assert perm.denied("edit_file", {"path": "app/main.py"}) is None


def test_no_rules_allows_everything():
    perm = Permissions([])
    assert perm.denied("run_command", {"command": "rm -rf /"}) is None


def test_load_missing_file_is_empty(tmp_path):
    perm = load_permissions(str(tmp_path))
    assert perm.rules == [] and perm.denied("run_command", {"command": "rm x"}) is None


def test_load_parses_yaml_forms(tmp_path):
    (tmp_path / ".vortocode").mkdir()
    (tmp_path / ".vortocode" / "permissions.yaml").write_text(
        'deny:\n'
        '  - web_fetch\n'                       # 字符串无冒号 → 整工具
        '  - "run_command: rm *"\n'             # 字符串 tool: glob
        '  - edit_file: "*/secrets/*"\n',       # dict 形式
        encoding="utf-8")
    perm = load_permissions(str(tmp_path))
    assert ("web_fetch", None) in perm.rules
    assert ("run_command", "rm *") in perm.rules
    assert ("edit_file", "*/secrets/*") in perm.rules
    assert perm.denied("run_command", {"command": "rm -rf x"}) is not None
    assert perm.denied("web_fetch", {"url": "http://x"}) is not None


def test_load_broken_yaml_safe(tmp_path):
    (tmp_path / ".vortocode").mkdir()
    (tmp_path / ".vortocode" / "permissions.yaml").write_text("deny: [unclosed\n", encoding="utf-8")
    perm = load_permissions(str(tmp_path))      # 坏配置 → 空、不抛
    assert perm.rules == []


# ---- 接进 MainAgent._run_tool：deny 硬拦、不分模式 ----

class _ScriptedLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    async def chat(self, messages, **kwargs):
        i = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return {"content": self.responses[i]}


@pytest.mark.asyncio
async def test_main_agent_blocks_denied_tool():
    from src.agents.main_agent import MainAgent, Tool
    ran = []

    async def handler(args):
        ran.append(args)
        return "ran"

    tool = Tool("run_command", "shell", {"command": "命令"}, handler, read_only=False)
    agent = MainAgent([tool], llm=_ScriptedLLM('{"tool":"run_command","args":{"command":"rm -rf build"}}',
                                               "好的，我换种方式。"),
                      permissions=Permissions([("run_command", "rm *")]))
    out = {"emit": []}
    await agent.run_turn("清理", mode="build", emit=lambda m: out["emit"].append(m))
    assert ran == []                                              # 被权限拦下、没真跑
    assert any("权限拦截" in m["content"] for m in agent.history)  # 拦截原因回灌了模型
    assert out["emit"] == ["好的，我换种方式。"]                    # agent 据此改道、继续


@pytest.mark.asyncio
async def test_main_agent_allows_unmatched():
    from src.agents.main_agent import MainAgent, Tool
    ran = []

    async def handler(args):
        ran.append(args)
        return "已执行 ls"

    tool = Tool("run_command", "shell", {"command": "命令"}, handler, read_only=False)
    agent = MainAgent([tool], llm=_ScriptedLLM('{"tool":"run_command","args":{"command":"ls"}}', "列好了。"),
                      permissions=Permissions([("run_command", "rm *")]))
    await agent.run_turn("看看", mode="build", emit=lambda _m: None)
    assert ran == [{"command": "ls"}]                            # 不匹配 deny → 正常执行
