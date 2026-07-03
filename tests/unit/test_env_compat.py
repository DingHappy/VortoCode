"""A3 · env 前缀兼容助手（src/env_compat.py）——VORTOCODE_* 优先、AUTODEV_* 回落 + 弃用告警。"""
import warnings

import pytest

from src import env_compat as ec


def test_new_key_wins(monkeypatch):
    monkeypatch.setenv("VORTOCODE_FOO", "new")
    monkeypatch.setenv("AUTODEV_FOO", "old")
    assert ec.env_compat("VORTOCODE_FOO", "AUTODEV_FOO") == "new"


def test_falls_back_to_old_with_deprecation_warning(monkeypatch):
    monkeypatch.delenv("VORTOCODE_BAR", raising=False)
    monkeypatch.setenv("AUTODEV_BAR", "legacy")
    ec._warned.discard("AUTODEV_BAR")                      # 清一次去重状态，确保能观察到告警
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        assert ec.env_compat("VORTOCODE_BAR", "AUTODEV_BAR") == "legacy"
    assert any(issubclass(x.category, DeprecationWarning) and "AUTODEV_BAR" in str(x.message) for x in w)


def test_default_when_neither(monkeypatch):
    monkeypatch.delenv("VORTOCODE_BAZ", raising=False)
    monkeypatch.delenv("AUTODEV_BAZ", raising=False)
    assert ec.env_compat("VORTOCODE_BAZ", "AUTODEV_BAZ", "fallback") == "fallback"
    assert ec.env_compat("VORTOCODE_BAZ", "AUTODEV_BAZ") is None


def test_dedup_warning_only_once(monkeypatch):
    monkeypatch.delenv("VORTOCODE_QUX", raising=False)
    monkeypatch.setenv("AUTODEV_QUX", "v")
    ec._warned.discard("AUTODEV_QUX")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        ec.env_compat("VORTOCODE_QUX", "AUTODEV_QUX")
        ec.env_compat("VORTOCODE_QUX", "AUTODEV_QUX")      # 第二次不该再告警
    assert sum(1 for x in w if issubclass(x.category, DeprecationWarning)) == 1


@pytest.mark.parametrize("key", ["VORTOCODE_API_TOKEN", "VORTOCODE_ENABLE_SHELL",
                                 "VORTOCODE_ALLOW_INSECURE_BIND"])
def test_security_vars_honor_new_prefix(monkeypatch, key):
    """安全敏感 env（token/shell/bind）读取已切到新前缀。"""
    from src.web import auth, server
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "tok")
    assert auth.get_api_token() == "tok"                   # server 的 fail-closed 守卫也走它
    monkeypatch.setenv("VORTOCODE_ENABLE_SHELL", "1")
    assert auth.shell_enabled() is True
    # 绑非本地但已设 token → 放行（新前缀生效）
    assert server._insecure_bind_reason("0.0.0.0") == ""


def test_old_prefix_still_works_end_to_end(monkeypatch):
    """兼容期：只设旧前缀 AUTODEV_ 仍生效（回落）。"""
    from src.web import auth
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    monkeypatch.setenv("AUTODEV_API_TOKEN", "legacy-tok")
    assert auth.get_api_token() == "legacy-tok"
