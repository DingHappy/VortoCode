"""vc doctor 自检（b3 PR-6）单测——离线：网络检查 mock，逐项级别与汇总退出码。"""

import pytest

pytest.importorskip("aiohttp")

from src.gateway import doctor  # noqa: E402


def test_check_git_in_repo(tmp_path):
    import subprocess
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    assert doctor._check_git(str(tmp_path)).level == "ok"
    assert doctor._check_git(str(tmp_path / "nowhere")).level in ("warn", "fail")


def test_check_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234")
    c = doctor._check_api_key()
    assert c.level == "ok" and "1234" in c.detail             # 只露尾 4 位
    assert "sk-test" not in c.detail                          # 不整段泄漏
    monkeypatch.delenv("OPENAI_API_KEY")
    assert doctor._check_api_key().level == "fail"


@pytest.mark.asyncio
async def test_check_serve_down_is_warn_not_fail(monkeypatch):
    monkeypatch.setenv("VORTOCODE_SERVE_URL", "http://127.0.0.1:9")   # 必然拒连
    c = await doctor._check_serve()
    assert c.level == "warn" and "回退" in c.detail            # serve 不在只算 warn（attach 会回退）


@pytest.mark.asyncio
async def test_check_relay_no_key_skips(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert (await doctor._check_relay()).level == "warn"


def test_check_permissions(tmp_path):
    assert doctor._check_permissions(str(tmp_path)).level == "ok"     # 没文件 = 默认面
    d = tmp_path / ".vortocode"
    d.mkdir()
    (d / "permissions.yaml").write_text("deny: [run_command]", encoding="utf-8")
    assert doctor._check_permissions(str(tmp_path)).level == "ok"


def test_check_permissions_bad_yaml_is_fail(tmp_path):
    """坏 YAML 必须报 fail（评审 #142：load_permissions 为运行时安全吞错、静默降级成空权限——
    doctor 借它检查等于永远绿；自检要 strict parse，把静默降级暴露出来）。"""
    d = tmp_path / ".vortocode"
    d.mkdir()
    (d / "permissions.yaml").write_text("deny: [unclosed", encoding="utf-8")   # 评审的复现输入
    c = doctor._check_permissions(str(tmp_path))
    assert c.level == "fail" and "降级" in c.detail
    (d / "permissions.yaml").write_text("- 只是个列表\n- 不是映射", encoding="utf-8")
    c2 = doctor._check_permissions(str(tmp_path))
    assert c2.level == "fail" and "结构不对" in c2.detail


def test_check_sandbox_reports_isolated_off_and_unavailable(monkeypatch):
    from src.agents import sandbox as sb
    monkeypatch.delenv("VORTOCODE_SANDBOX", raising=False)
    monkeypatch.setattr(sb, "sandbox_backend", lambda: "seatbelt")
    assert doctor._check_sandbox().level == "ok"

    monkeypatch.setenv("VORTOCODE_SANDBOX", "off")
    assert doctor._check_sandbox().level == "warn"

    monkeypatch.setenv("VORTOCODE_SANDBOX", "required")
    monkeypatch.setattr(sb, "sandbox_backend", lambda: "")
    assert doctor._check_sandbox().level == "fail"


def test_check_im_unconfigured_is_warn(monkeypatch):
    for k in ("VORTOCODE_TG_TOKEN", "VORTOCODE_TG_OWNER_ID",
              "VORTOCODE_DD_CLIENT_ID", "VORTOCODE_DD_CLIENT_SECRET", "VORTOCODE_DD_OWNER_ID"):
        monkeypatch.delenv(k, raising=False)
    c = doctor._check_im()
    assert c.level == "warn" and "可选" in c.detail


def test_summarize_exit_codes():
    ok = doctor.Check("a", "ok", "好")
    warn = doctor.Check("b", "warn", "缺配置")
    fail = doctor.Check("c", "fail", "硬伤")
    text, code = doctor.summarize([ok, warn])
    assert code == 0 and "无硬伤" in text and "1 项待配置" in text   # warn 不算失败
    text2, code2 = doctor.summarize([ok, fail])
    assert code2 == 1 and "1 项硬伤" in text2


@pytest.mark.asyncio
async def test_run_checks_order_stable_and_isolated(tmp_path, monkeypatch):
    """八项齐、顺序稳定；单项炸不拖全体（gh 检查抛异常 → 记 fail、其余照常）。"""
    monkeypatch.setenv("VORTOCODE_SERVE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENAI_API_BASE", "http://127.0.0.1:9/v1")   # 中转站也指向死端口（离线）

    def _boom():
        raise RuntimeError("gh 炸了")

    monkeypatch.setattr(doctor, "_check_gh", _boom)
    checks = await doctor.run_checks(str(tmp_path))
    assert [c.name for c in checks] == ["git", "api-key", "relay", "serve",
                                        "sandbox", "permissions", "gh", "im"]
    gh = next(c for c in checks if c.name == "gh")
    assert gh.level == "fail" and "检查本身出错" in gh.detail          # 炸的那项记 fail
    assert checks[-1].name == "im"                                    # 后续项没被拖死
