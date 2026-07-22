"""子进程凭据隔离（信任地基）的行为测试。

- 操作密钥不进子进程 env；
- allowlist 逃生口（VORTOCODE_ENV_PASSTHROUGH）能把显式放行项放回；
- 精确名单不误伤带 TOKEN/KEY 字样的非密钥项（对抗通配符的回归钉）；
- 起真子进程验证：即便 VORTOCODE_SANDBOX=off 的宿主机 escape hatch，密钥也进不去。

全部离线确定性：非沙箱路径起 echo 子进程，不依赖 sandbox-exec/bwrap 可用。
"""
import pytest

from src.agents.sandbox import _STRIPPED_ENV_KEYS, child_env


# ── child_env dict 层 ────────────────────────────────────────────────

def test_operating_secrets_stripped(monkeypatch):
    monkeypatch.delenv("VORTOCODE_ENV_PASSTHROUGH", raising=False)
    for key in _STRIPPED_ENV_KEYS:
        monkeypatch.setenv(key, "sk-proof-secret")
    env = child_env()
    for key in _STRIPPED_ENV_KEYS:
        assert key not in env, f"{key} 必须被剥出子进程 env"


def test_passthrough_allowlist_restores_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proof")
    monkeypatch.setenv("VORTOCODE_ENV_PASSTHROUGH", "OPENAI_API_KEY")
    assert child_env()["OPENAI_API_KEY"] == "sk-proof"   # 主人显式放行 → 穿透


def test_allowlist_is_comma_separated_and_trims(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-a")
    monkeypatch.setenv("VORTOCODE_TG_TOKEN", "tg-b")
    monkeypatch.setenv("VORTOCODE_ENV_PASSTHROUGH", " OPENAI_API_KEY , VORTOCODE_TG_TOKEN ")
    env = child_env()
    assert env["OPENAI_API_KEY"] == "sk-a" and env["VORTOCODE_TG_TOKEN"] == "tg-b"


def test_non_secret_vars_preserved(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    assert child_env().get("PATH") == "/usr/bin"   # 否则子进程/wrapper 找不到程序


def test_wildcard_false_positives_not_stripped(monkeypatch):
    """精确名单不误伤：带 TOKEN/KEY 字样但非密钥的项必须留下（对抗 *KEY*/*TOKEN* 通配符）。"""
    monkeypatch.delenv("VORTOCODE_ENV_PASSTHROUGH", raising=False)
    monkeypatch.setenv("VORTOCODE_CRON_TOKEN_BUDGET", "500")
    monkeypatch.setenv("VORTOCODE_MAX_CONTEXT_TOKENS", "128000")
    monkeypatch.setenv("TEXTUAL_DISABLE_KITTY_KEY", "1")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-task-token")   # 任务令牌：gh 子命令要用，不剥
    env = child_env()
    assert env["VORTOCODE_CRON_TOKEN_BUDGET"] == "500"
    assert env["VORTOCODE_MAX_CONTEXT_TOKENS"] == "128000"
    assert env["TEXTUAL_DISABLE_KITTY_KEY"] == "1"
    assert env["GITHUB_TOKEN"] == "gh-task-token"


# ── 真子进程行为（非沙箱 escape hatch 路径也必须剥）──────────────────────

def test_subprocess_cannot_read_secret(monkeypatch, tmp_path):
    """起真子进程：即便 VORTOCODE_SANDBOX=off 的宿主机路径，密钥也进不去。"""
    from src.agents.shell import run_command

    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-leak")
    monkeypatch.setenv("VORTOCODE_SANDBOX", "off")
    monkeypatch.delenv("VORTOCODE_ENV_PASSTHROUGH", raising=False)
    r = run_command(str(tmp_path), "echo KEY=[$OPENAI_API_KEY]")
    assert r["ok"], r["output"]
    assert "KEY=[]" in r["output"], r["output"]


def test_subprocess_sees_passthrough(monkeypatch, tmp_path):
    """allowlist 放行的项能被子进程读到（逃生口真的通）。"""
    from src.agents.shell import run_command

    monkeypatch.setenv("OPENAI_API_KEY", "sk-allowed")
    monkeypatch.setenv("VORTOCODE_ENV_PASSTHROUGH", "OPENAI_API_KEY")
    monkeypatch.setenv("VORTOCODE_SANDBOX", "off")
    r = run_command(str(tmp_path), "echo KEY=[$OPENAI_API_KEY]")
    assert r["ok"], r["output"]
    assert "KEY=[sk-allowed]" in r["output"], r["output"]


def test_subprocess_cannot_read_secret_through_sandbox(monkeypatch, tmp_path):
    """沙箱路径（seatbelt/bwrap）同样剥密钥——现有 off 路径之外补一条真沙箱钉，
    防将来重构 sandboxed_argv 时从别处重注父 env 而无测试变红（对抗审查 F6）。"""
    from src.agents.sandbox import resolve_sandbox
    from src.agents.shell import run_command

    monkeypatch.setenv("OPENAI_API_KEY", "sk-sandbox-leak")
    monkeypatch.delenv("VORTOCODE_SANDBOX", raising=False)          # 走 auto → 有则用沙箱
    monkeypatch.delenv("VORTOCODE_ENV_PASSTHROUGH", raising=False)
    if not resolve_sandbox().isolated:
        pytest.skip("本机无可用 OS 沙箱（seatbelt/bwrap），跳过沙箱路径断言")
    r = run_command(str(tmp_path), "echo KEY=[$OPENAI_API_KEY]")
    assert r["ok"], r["output"]
    assert "KEY=[]" in r["output"], r["output"]
