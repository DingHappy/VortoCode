"""A3 · env 前缀兼容助手（src/env_compat.py）——VORTOONE_* 优先、AUTODEV_* 回落 + 弃用告警。"""
import os
import warnings

import pytest

from src import env_compat as ec


def test_new_key_wins(monkeypatch):
    monkeypatch.setenv("VORTOONE_FOO", "new")
    monkeypatch.setenv("AUTODEV_FOO", "old")
    assert ec.env_compat("VORTOONE_FOO", "AUTODEV_FOO") == "new"


def test_falls_back_to_old_with_deprecation_warning(monkeypatch):
    monkeypatch.delenv("VORTOONE_BAR", raising=False)
    monkeypatch.setenv("AUTODEV_BAR", "legacy")
    ec._warned.discard("AUTODEV_BAR")                      # 清一次去重状态，确保能观察到告警
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        assert ec.env_compat("VORTOONE_BAR", "AUTODEV_BAR") == "legacy"
    assert any(issubclass(x.category, DeprecationWarning) and "AUTODEV_BAR" in str(x.message) for x in w)


def test_default_when_neither(monkeypatch):
    monkeypatch.delenv("VORTOONE_BAZ", raising=False)
    monkeypatch.delenv("AUTODEV_BAZ", raising=False)
    assert ec.env_compat("VORTOONE_BAZ", "AUTODEV_BAZ", "fallback") == "fallback"
    assert ec.env_compat("VORTOONE_BAZ", "AUTODEV_BAZ") is None


def test_dedup_warning_only_once(monkeypatch):
    monkeypatch.delenv("VORTOONE_QUX", raising=False)
    monkeypatch.setenv("AUTODEV_QUX", "v")
    ec._warned.discard("AUTODEV_QUX")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        ec.env_compat("VORTOONE_QUX", "AUTODEV_QUX")
        ec.env_compat("VORTOONE_QUX", "AUTODEV_QUX")      # 第二次不该再告警
    assert sum(1 for x in w if issubclass(x.category, DeprecationWarning)) == 1


@pytest.mark.parametrize("key", ["VORTOONE_API_TOKEN", "VORTOONE_ENABLE_SHELL",
                                 "VORTOONE_ALLOW_INSECURE_BIND"])
def test_security_vars_honor_new_prefix(monkeypatch, key):
    """安全敏感 env（token/shell/bind）读取已切到新前缀。"""
    from src.web import auth, server
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)
    monkeypatch.setenv("VORTOONE_API_TOKEN", "tok")
    assert auth.get_api_token() == "tok"                   # server 的 fail-closed 守卫也走它
    monkeypatch.setenv("VORTOONE_ENABLE_SHELL", "1")
    from src.sandbox.policy import shell_enabled
    assert shell_enabled() is True
    # 绑非本地但已设 token → 放行（新前缀生效）
    assert server._insecure_bind_reason("0.0.0.0") == ""


# 告警本身已由 test_falls_back_to_old_with_deprecation_warning 断言；这里只验回落值，别让预期告警漏进汇总。
@pytest.mark.filterwarnings("ignore:环境变量 AUTODEV_API_TOKEN 已弃用:DeprecationWarning")
def test_old_prefix_still_works_end_to_end(monkeypatch):
    """兼容期：只设旧前缀 AUTODEV_ 仍生效（回落）。"""
    from src.web import auth
    monkeypatch.delenv("VORTOONE_API_TOKEN", raising=False)
    monkeypatch.setenv("AUTODEV_API_TOKEN", "legacy-tok")
    assert auth.get_api_token() == "legacy-tok"


# ── ADR-018 批次 B：VORTOCODE_* → VORTOONE_*，进程起来统一换名 ──────────────────

def test_legacy_name_moves_to_new_name_and_is_removed():
    env = {"VORTOCODE_IM": "dingtalk", "PATH": "/bin"}
    assert ec.adopt_legacy_env(env) == ["VORTOCODE_IM"]
    assert env == {"VORTOONE_IM": "dingtalk", "PATH": "/bin"}


def test_new_name_wins_when_both_are_set_and_empty_string_counts_as_set():
    """新名字设成空串是「明确关掉」（Desktop 就这么钉 VORTOONE_IM=""），旧名字不能把它顶回来。"""
    env = {"VORTOONE_IM": "", "VORTOCODE_IM": "dingtalk",
           "VORTOONE_ENABLE_SHELL": "0", "VORTOCODE_ENABLE_SHELL": "1"}
    ec.adopt_legacy_env(env)
    assert env == {"VORTOONE_IM": "", "VORTOONE_ENABLE_SHELL": "0"}


def test_children_never_see_the_legacy_name(monkeypatch):
    """清开关的地方只清新名字；旧名字已经不在环境里，子进程不会再把开关读回来。"""
    import subprocess
    import sys
    monkeypatch.setenv("VORTOCODE_ENABLE_DESKTOP_CONTROL", "1")
    ec.adopt_legacy_env()
    child_env = dict(os.environ)
    child_env.pop("VORTOONE_ENABLE_DESKTOP_CONTROL", None)
    out = subprocess.run([sys.executable, "-c",
                          "import os; print(sorted(k for k in os.environ if 'DESKTOP_CONTROL' in k))"],
                         env=child_env, capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"
    assert "VORTOCODE_ENABLE_DESKTOP_CONTROL" in ec.legacy_env_names()


def test_importing_the_package_adopts_legacy_names(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    out = subprocess.run([sys.executable, "-c",
                          "import os, src; print(os.environ.get('VORTOONE_CRON'), 'VORTOCODE_CRON' in os.environ)"],
                         cwd=root, env={**os.environ, "VORTOCODE_CRON": "1"},
                         capture_output=True, text=True, check=True).stdout
    assert out.split() == ["1", "False"]


def test_env_file_with_legacy_names_is_adopted_without_overriding(tmp_path, monkeypatch):
    """`.env` 比包导入晚加载：里面的旧名字也要换；进程环境里已有的值照旧优先。"""
    pytest.importorskip("dotenv")
    env_file = tmp_path / ".env"
    env_file.write_text("VORTOCODE_HEARTBEAT=1\nVORTOCODE_CRON=1\n", encoding="utf-8")
    monkeypatch.delenv("VORTOONE_HEARTBEAT", raising=False)
    monkeypatch.setenv("VORTOONE_CRON", "0")
    assert ec.load_env_file(env_file) is True
    try:
        assert os.environ["VORTOONE_HEARTBEAT"] == "1"
        assert os.environ["VORTOONE_CRON"] == "0"
        assert not [k for k in os.environ if k.startswith("VORTOCODE_")]
    finally:
        os.environ.pop("VORTOONE_HEARTBEAT", None)
