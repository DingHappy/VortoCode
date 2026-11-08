"""子进程凭据隔离（信任地基）的行为测试。

- 操作密钥不进子进程 env；
- allowlist 逃生口（VORTOONE_ENV_PASSTHROUGH）能把显式放行项放回；
- 精确名单不误伤带 TOKEN/KEY 字样的非密钥项（对抗通配符的回归钉）；
- 起真子进程验证：即便 VORTOONE_SANDBOX=off 的宿主机 escape hatch，密钥也进不去。

全部离线确定性：非沙箱路径起 echo 子进程，不依赖 sandbox-exec/bwrap 可用。
"""
import pytest

from src.sandbox.policy import _STRIPPED_ENV_KEYS, child_env


@pytest.mark.parametrize("name", ["VORTOONE_CODEX_REPORT_TOKEN", "VORTOONE_PROVIDER_RESEARCH_KEY",
                                 "VORTOONE_PROVIDER_RETIRED_KEY", "VORTOONE_OPS_TOKEN",
                                 "VORTOONE_VOICE_MCP_TOKEN"])
def test_runtime_control_and_named_model_credentials_do_not_escape(monkeypatch, name):
    from src.sandbox.policy import is_stripped_env
    monkeypatch.delenv("VORTOONE_ENV_PASSTHROUGH", raising=False)
    monkeypatch.setenv(name, "synthetic-runtime-secret")
    assert is_stripped_env(name) and name not in child_env()


def test_host_declared_secret_names_are_filtered_and_explicit_passthrough_is_exact(monkeypatch):
    from src.sandbox.policy import register_secret_env_names, is_stripped_env
    register_secret_env_names(["BOUNDARY_TEST_BOT_TOKEN"])
    monkeypatch.setenv("BOUNDARY_TEST_BOT_TOKEN", "synthetic-channel-secret")
    monkeypatch.setenv("BOUNDARY_TEST_TOKEN_BUDGET", "12")
    monkeypatch.delenv("VORTOONE_ENV_PASSTHROUGH", raising=False)
    assert is_stripped_env("BOUNDARY_TEST_BOT_TOKEN")
    assert "BOUNDARY_TEST_BOT_TOKEN" not in child_env()
    assert child_env()["BOUNDARY_TEST_TOKEN_BUDGET"] == "12"
    monkeypatch.setenv("VORTOONE_ENV_PASSTHROUGH", "BOUNDARY_TEST_BOT_TOKEN_typo")
    assert "BOUNDARY_TEST_BOT_TOKEN" not in child_env()
    monkeypatch.setenv("VORTOONE_ENV_PASSTHROUGH", "BOUNDARY_TEST_BOT_TOKEN")
    assert child_env()["BOUNDARY_TEST_BOT_TOKEN"] == "synthetic-channel-secret"


def test_secret_name_registration_is_validated_before_any_change(monkeypatch):
    from src.sandbox.policy import register_secret_env_names, is_stripped_env
    monkeypatch.delenv("VORTOONE_ENV_PASSTHROUGH", raising=False)
    with pytest.raises(ValueError):
        register_secret_env_names(["BOUNDARY_TEST_ATOMIC_TOKEN", "not an env name"])
    assert not is_stripped_env("BOUNDARY_TEST_ATOMIC_TOKEN")
    with pytest.raises(ValueError):
        register_secret_env_names("BOUNDARY_TEST_STRING_TOKEN")


def test_host_secret_cannot_be_reintroduced_by_external_cli_settings(tmp_path, monkeypatch):
    import json
    from src.agents.external_workers import user_config
    from src.sandbox.policy import register_secret_env_names
    register_secret_env_names(["BOUNDARY_TEST_REINJECTION_TOKEN"])
    (tmp_path / "settings.json").write_text(json.dumps({"env": {
        "BOUNDARY_TEST_REINJECTION_TOKEN": "synthetic-channel-secret",
        "VORTOONE_PROVIDER_PERSONAL_KEY": "synthetic-model-secret",
        "VORTOONE_CODEX_REPORT_TOKEN": "synthetic-control-secret",
        "ANTHROPIC_AUTH_TOKEN": "synthetic-cli-owned-secret"}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("VORTOONE_ENV_PASSTHROUGH", raising=False)
    _, env = user_config("claude")
    assert env == {"ANTHROPIC_AUTH_TOKEN": "synthetic-cli-owned-secret"}


def test_actual_command_sees_no_runtime_secrets_and_keeps_task_environment(tmp_path, monkeypatch):
    from src.agents.shell import run_command
    from src.sandbox.policy import register_secret_env_names
    register_secret_env_names(["BOUNDARY_TEST_COMMAND_TOKEN"])
    for key in ("BOUNDARY_TEST_COMMAND_TOKEN", "VORTOONE_PROVIDER_COMMAND_KEY", "VORTOONE_CODEX_REPORT_TOKEN"):
        monkeypatch.setenv(key, "synthetic-not-a-live-secret")
    monkeypatch.setenv("VORTOONE_SANDBOX", "off")
    monkeypatch.setenv("VORTOONE_CRON_TOKEN_BUDGET", "12")
    monkeypatch.delenv("VORTOONE_ENV_PASSTHROUGH", raising=False)
    result = run_command(str(tmp_path),
        'if [ -n "$BOUNDARY_TEST_COMMAND_TOKEN$VORTOONE_PROVIDER_COMMAND_KEY$VORTOONE_CODEX_REPORT_TOKEN" ]; '
        'then echo secret_present; else echo secret_absent; fi; echo budget=$VORTOONE_CRON_TOKEN_BUDGET')
    assert result["ok"] and "secret_absent" in result["output"] and "budget=12" in result["output"]
    assert "synthetic-not-a-live-secret" not in result["output"]


async def test_stdio_mcp_inherits_filtered_environment_with_its_own_explicit_settings(monkeypatch):
    from types import SimpleNamespace
    from src.tools import mcp_client
    from src.sandbox.policy import register_secret_env_names
    register_secret_env_names(["BOUNDARY_TEST_MCP_TOKEN"])
    monkeypatch.setenv("BOUNDARY_TEST_MCP_TOKEN", "synthetic-host-secret")
    monkeypatch.setenv("VORTOONE_PROVIDER_MCP_KEY", "synthetic-model-secret")
    monkeypatch.setenv("VORTOONE_CODEX_REPORT_TOKEN", "synthetic-control-secret")
    monkeypatch.delenv("VORTOONE_ENV_PASSTHROUGH", raising=False)
    captured = {}
    async def spawn(*_args, **kwargs):
        captured.update(kwargs["env"])
        return SimpleNamespace()
    async def rpc(*_args, **_kwargs):
        return {"result": {}}
    monkeypatch.setattr(mcp_client.asyncio, "create_subprocess_exec", spawn)
    client = mcp_client.StdioMCPClient("fixture", "unused", env={"PLUGIN_SETTING": "explicit"})
    monkeypatch.setattr(client, "_rpc", rpc)
    assert await client.connect()
    assert not {"BOUNDARY_TEST_MCP_TOKEN", "VORTOONE_PROVIDER_MCP_KEY", "VORTOONE_CODEX_REPORT_TOKEN"} & captured.keys()
    assert captured["PLUGIN_SETTING"] == "explicit"


# ── child_env dict 层 ────────────────────────────────────────────────

def test_operating_secrets_stripped(monkeypatch):
    monkeypatch.delenv("VORTOONE_ENV_PASSTHROUGH", raising=False)
    for key in _STRIPPED_ENV_KEYS:
        monkeypatch.setenv(key, "sk-proof-secret")
    env = child_env()
    for key in _STRIPPED_ENV_KEYS:
        assert key not in env, f"{key} 必须被剥出子进程 env"


def test_passthrough_allowlist_restores_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proof")
    monkeypatch.setenv("VORTOONE_ENV_PASSTHROUGH", "OPENAI_API_KEY")
    assert child_env()["OPENAI_API_KEY"] == "sk-proof"   # 主人显式放行 → 穿透


def test_allowlist_is_comma_separated_and_trims(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-a")
    monkeypatch.setenv("VORTOONE_TG_TOKEN", "tg-b")
    monkeypatch.setenv("VORTOONE_ENV_PASSTHROUGH", " OPENAI_API_KEY , VORTOONE_TG_TOKEN ")
    env = child_env()
    assert env["OPENAI_API_KEY"] == "sk-a" and env["VORTOONE_TG_TOKEN"] == "tg-b"


def test_non_secret_vars_preserved(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    assert child_env().get("PATH") == "/usr/bin"   # 否则子进程/wrapper 找不到程序


def test_wildcard_false_positives_not_stripped(monkeypatch):
    """精确名单不误伤：带 TOKEN/KEY 字样但非密钥的项必须留下（对抗 *KEY*/*TOKEN* 通配符）。"""
    monkeypatch.delenv("VORTOONE_ENV_PASSTHROUGH", raising=False)
    monkeypatch.setenv("VORTOONE_CRON_TOKEN_BUDGET", "500")
    monkeypatch.setenv("VORTOONE_MAX_CONTEXT_TOKENS", "128000")
    monkeypatch.setenv("TEXTUAL_DISABLE_KITTY_KEY", "1")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-task-token")   # 任务令牌：gh 子命令要用，不剥
    env = child_env()
    assert env["VORTOONE_CRON_TOKEN_BUDGET"] == "500"
    assert env["VORTOONE_MAX_CONTEXT_TOKENS"] == "128000"
    assert env["TEXTUAL_DISABLE_KITTY_KEY"] == "1"
    assert env["GITHUB_TOKEN"] == "gh-task-token"


# ── 真子进程行为（非沙箱 escape hatch 路径也必须剥）──────────────────────

def test_subprocess_cannot_read_secret(monkeypatch, tmp_path):
    """起真子进程：即便 VORTOONE_SANDBOX=off 的宿主机路径，密钥也进不去。"""
    from src.agents.shell import run_command

    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-leak")
    monkeypatch.setenv("VORTOONE_SANDBOX", "off")
    monkeypatch.delenv("VORTOONE_ENV_PASSTHROUGH", raising=False)
    r = run_command(str(tmp_path), "echo KEY=[$OPENAI_API_KEY]")
    assert r["ok"], r["output"]
    assert "KEY=[]" in r["output"], r["output"]


def test_subprocess_sees_passthrough(monkeypatch, tmp_path):
    """allowlist 放行的项能被子进程读到（逃生口真的通）。"""
    from src.agents.shell import run_command

    monkeypatch.setenv("OPENAI_API_KEY", "sk-allowed")
    monkeypatch.setenv("VORTOONE_ENV_PASSTHROUGH", "OPENAI_API_KEY")
    monkeypatch.setenv("VORTOONE_SANDBOX", "off")
    r = run_command(str(tmp_path), "echo KEY=[$OPENAI_API_KEY]")
    assert r["ok"], r["output"]
    assert "KEY=[sk-allowed]" in r["output"], r["output"]


def test_subprocess_cannot_read_secret_through_sandbox(monkeypatch, tmp_path):
    """沙箱路径（seatbelt/bwrap）同样剥密钥——现有 off 路径之外补一条真沙箱钉，
    防将来重构 sandboxed_argv 时从别处重注父 env 而无测试变红（对抗审查 F6）。"""
    from src.sandbox.policy import resolve_sandbox
    from src.agents.shell import run_command

    monkeypatch.setenv("OPENAI_API_KEY", "sk-sandbox-leak")
    monkeypatch.delenv("VORTOONE_SANDBOX", raising=False)          # 走 auto → 有则用沙箱
    monkeypatch.delenv("VORTOONE_ENV_PASSTHROUGH", raising=False)
    if not resolve_sandbox().isolated:
        pytest.skip("本机无可用 OS 沙箱（seatbelt/bwrap），跳过沙箱路径断言")
    r = run_command(str(tmp_path), "echo KEY=[$OPENAI_API_KEY]")
    assert r["ok"], r["output"]
    assert "KEY=[]" in r["output"], r["output"]
