"""多助手名册（gateway/roster）——起服务前把配错的地方查出来。

为什么值得一个专门的体检：多助手配错的表现**全都是同一个样子——机器人装死**。
而"装死"是这套系统里最贵的故障：人的第一反应是"坏了/连不上"，排查能耗一整晚
（真机 2026-07-26 的白名单漏配就是这样）。把"装死"翻译成一句人话，价值全在这里。

最要紧的一条是**凭证复用**：复制一份 env 改个名字、CLIENT_ID 忘了换 → 两条桥抢同一个
机器人，消息随机落到其中一个。它不报错，只是时灵时不灵——最难查的那种。
"""

import stat
from pathlib import Path

import pytest

from src.gateway import roster as R

SECRET = "SUPER-SECRET-CLIENT-ID-do-not-print"


def _env(path: Path, *, client_id="cid", secret="sec", owner="owner1", allow=None,
         mode=0o600) -> Path:
    lines = [f"VORTOCODE_DD_CLIENT_ID={client_id}",
             f"VORTOCODE_DD_CLIENT_SECRET={secret}",
             f"VORTOCODE_DD_OWNER_ID={owner}"]
    if allow is not None:
        lines.append(f"VORTOCODE_DD_ALLOW_FROM={allow}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


def _roster(tmp_path: Path, body: str) -> R.Roster:
    (tmp_path / ".vortocode").mkdir(exist_ok=True)
    (tmp_path / ".vortocode" / "assistants.yaml").write_text(body, encoding="utf-8")
    return R.load_roster(str(tmp_path))


def _levels(findings, level):
    return [f for f in findings if f.level == level]


# --------------------------------------------------------------- 基本形态
def test_missing_roster_is_not_an_error(tmp_path):
    """没配多助手是完全正常的状态——不该报错、不该有结论。"""
    r = R.load_roster(str(tmp_path))
    assert r.assistants == [] and r.errors == []
    assert R.check_roster(r) == []


def test_broken_yaml_reports_instead_of_crashing(tmp_path):
    r = _roster(tmp_path, "assistants: [oops\n  - bad")
    assert r.errors and "名册" in R.check_roster(r)[0].who


def test_wrong_shape_is_reported(tmp_path):
    r = _roster(tmp_path, "something_else: 1\n")
    assert r.errors and "assistants" in r.errors[0]


def test_healthy_roster_passes(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _env(tmp_path / "a.env")
    r = _roster(tmp_path, f"""
assistants:
  - name: alice
    channel: dingtalk
    workspace: {ws}
    env_file: {tmp_path / 'a.env'}
""")
    findings = R.check_roster(r)
    assert _levels(findings, "fail") == [], [f.detail for f in findings]
    ok, _ = R.summarize(findings)
    assert ok


# --------------------------------------------------------------- 旗舰检查：凭证复用
def test_shared_bot_credentials_is_a_hard_fail(tmp_path):
    """两个助手共用一个机器人 → 两条桥抢同一条长连接。这是多助手最容易踩的坑。"""
    for who in ("alice", "bob"):
        (tmp_path / who).mkdir()
        _env(tmp_path / f"{who}.env", client_id="SAME-BOT", owner=who)
    r = _roster(tmp_path, f"""
assistants:
  - name: alice
    channel: dingtalk
    workspace: {tmp_path / 'alice'}
    env_file: {tmp_path / 'alice.env'}
  - name: bob
    channel: dingtalk
    workspace: {tmp_path / 'bob'}
    env_file: {tmp_path / 'bob.env'}
""")
    bad = _levels(R.check_roster(r), "fail")
    assert bad, "共用同一个机器人凭证却放行了"
    assert any("共用同一个" in f.detail for f in bad)
    assert any("alice" in f.who and "bob" in f.who for f in bad)


def test_distinct_credentials_are_fine(tmp_path):
    """反向对照：各建各的应用就不该报——误报会逼人把真检查关掉。"""
    for who in ("alice", "bob"):
        (tmp_path / who).mkdir()
        _env(tmp_path / f"{who}.env", client_id=f"bot-{who}", owner=who)
    r = _roster(tmp_path, f"""
assistants:
  - name: alice
    channel: dingtalk
    workspace: {tmp_path / 'alice'}
    env_file: {tmp_path / 'alice.env'}
  - name: bob
    channel: dingtalk
    workspace: {tmp_path / 'bob'}
    env_file: {tmp_path / 'bob.env'}
""")
    assert _levels(R.check_roster(r), "fail") == []


def test_shared_workspace_is_a_hard_fail(tmp_path):
    """共用工作区 → 人设、会话历史、记忆全串在一起。"""
    ws = tmp_path / "shared"
    ws.mkdir()
    for who in ("alice", "bob"):
        _env(tmp_path / f"{who}.env", client_id=f"bot-{who}", owner=who)
    r = _roster(tmp_path, f"""
assistants:
  - name: alice
    channel: dingtalk
    workspace: {ws}
    env_file: {tmp_path / 'alice.env'}
  - name: bob
    channel: dingtalk
    workspace: {ws}
    env_file: {tmp_path / 'bob.env'}
""")
    bad = _levels(R.check_roster(r), "fail")
    assert any("共用工作区" in f.detail for f in bad)


# --------------------------------------------------------------- 单个助手的配错
def test_missing_credential_keys_are_named(tmp_path):
    """缺哪个键要说出来（桥会 fail-closed 拒启，光说"配错了"没用）。"""
    (tmp_path / "ws").mkdir()
    (tmp_path / "a.env").write_text("VORTOCODE_DD_CLIENT_ID=x\n", encoding="utf-8")
    r = _roster(tmp_path, f"""
assistants:
  - name: alice
    channel: dingtalk
    workspace: {tmp_path / 'ws'}
    env_file: {tmp_path / 'a.env'}
""")
    bad = _levels(R.check_roster(r), "fail")
    joined = " ".join(f.detail for f in bad)
    assert "VORTOCODE_DD_CLIENT_SECRET" in joined and "VORTOCODE_DD_OWNER_ID" in joined


def test_owner_missing_from_allowlist_is_a_hard_fail(tmp_path):
    """白名单配了却漏了自己——表现是「机器人不理人 + 确认永远超时」，极难猜。"""
    (tmp_path / "ws").mkdir()
    _env(tmp_path / "a.env", owner="me", allow="someone,else")
    r = _roster(tmp_path, f"""
assistants:
  - name: alice
    channel: dingtalk
    workspace: {tmp_path / 'ws'}
    env_file: {tmp_path / 'a.env'}
""")
    bad = _levels(R.check_roster(r), "fail")
    assert any("没有 owner 本人" in f.detail for f in bad)


def test_explicit_empty_allowlist_is_warned_not_failed(tmp_path):
    """空白名单是**合法**的 fail-closed 配法（"空 ≠ 不限制"），但多半不是想要的 → 警告，不判死。"""
    (tmp_path / "ws").mkdir()
    _env(tmp_path / "a.env", owner="me", allow="")
    r = _roster(tmp_path, f"""
assistants:
  - name: alice
    channel: dingtalk
    workspace: {tmp_path / 'ws'}
    env_file: {tmp_path / 'a.env'}
""")
    findings = R.check_roster(r)
    assert _levels(findings, "fail") == []
    assert any("拒绝一切入站" in f.detail for f in _levels(findings, "warn"))


def test_loose_env_permissions_warned(tmp_path):
    """env 里是 bot 的全权凭据；group/other 可读等于摊开给同机所有用户。"""
    (tmp_path / "ws").mkdir()
    _env(tmp_path / "a.env", mode=0o644)
    r = _roster(tmp_path, f"""
assistants:
  - name: alice
    channel: dingtalk
    workspace: {tmp_path / 'ws'}
    env_file: {tmp_path / 'a.env'}
""")
    assert any("权限过松" in f.detail for f in _levels(R.check_roster(r), "warn"))


@pytest.mark.parametrize("name", ["Alice", "a/b", "with space", "-lead", "", "x" * 40])
def test_unsafe_names_rejected(tmp_path, name):
    """名字会进 systemd 实例名、路径和 journal 标签——放宽只会换来奇怪的故障。"""
    (tmp_path / "ws").mkdir()
    _env(tmp_path / "a.env")
    r = _roster(tmp_path, f"""
assistants:
  - name: "{name}"
    channel: dingtalk
    workspace: {tmp_path / 'ws'}
    env_file: {tmp_path / 'a.env'}
""")
    if not r.assistants:                       # 空名字在装载期就被挡下
        assert r.errors
        return
    assert _levels(R.check_roster(r), "fail")


def test_duplicate_names_collide_on_systemd(tmp_path):
    (tmp_path / "ws").mkdir()
    _env(tmp_path / "a.env")
    r = _roster(tmp_path, f"""
assistants:
  - name: alice
    channel: dingtalk
    workspace: {tmp_path / 'ws'}
    env_file: {tmp_path / 'a.env'}
  - name: alice
    channel: dingtalk
    workspace: {tmp_path / 'ws'}
    env_file: {tmp_path / 'a.env'}
""")
    assert any("名字重复" in f.detail for f in _levels(R.check_roster(r), "fail"))


# --------------------------------------------------------------- 绝不泄漏凭证
def test_check_never_prints_credential_values(tmp_path):
    """**核心约束**：体检只报键名与"有没有撞车"，绝不把凭证值吐出来。

    这个模块天生要读一堆 secret 才能干活，一旦某条结论顺手把值拼进去，
    体检报告本身就成了泄漏面（它会被贴进聊天、粘进工单）。用行为断言钉死：
    把一个独特的 secret 灌进去，扫描**每一条结论**确认它不在里面。
    """
    for who in ("alice", "bob"):
        (tmp_path / who).mkdir()
        _env(tmp_path / f"{who}.env", client_id=SECRET, secret=SECRET, owner=SECRET,
             allow="nobody", mode=0o644)
    r = _roster(tmp_path, f"""
assistants:
  - name: alice
    channel: dingtalk
    workspace: {tmp_path / 'alice'}
    env_file: {tmp_path / 'alice.env'}
  - name: bob
    channel: dingtalk
    workspace: {tmp_path / 'bob'}
    env_file: {tmp_path / 'bob.env'}
""")
    findings = R.check_roster(r)
    assert findings, "什么都没查出来的话这条测试没有意义"
    assert _levels(findings, "fail"), "共用凭证 + 白名单漏人都该报"
    for f in findings:
        assert SECRET not in f.detail, f"结论里泄漏了凭证：{f.detail}"
        assert SECRET not in f.who


def test_env_template_has_no_values(tmp_path):
    """模板只给键名。给了示例值，人会照抄上线。"""
    for channel in ("dingtalk", "telegram"):
        text = R.env_template(channel)
        for key in R.REQUIRED_KEYS[channel]:
            assert f"{key}=" in text
            assert not text.split(f"{key}=")[1].splitlines()[0].strip(), "模板里带了值"


def test_env_template_rejects_unknown_channel():
    assert "未知通道" in R.env_template("wechat")


# --------------------------------------------------------------- systemd 契约
def test_unit_name_matches_the_template_file():
    """单元名必须和仓库里的模板文件对得上——对不上就是照文档抄也起不来。"""
    a = R.Assistant(name="alice", channel="dingtalk", workspace="/w", env_file="/e")
    assert a.unit == "vortocode-assistant@alice.service"
    tpl = Path(__file__).resolve().parents[2] / "examples" / "systemd" \
        / "vortocode-assistant@.service.example"
    assert tpl.is_file(), "systemd 模板文件不在了，名册给的启用命令就成了空头支票"
    body = tpl.read_text(encoding="utf-8")
    assert "%i" in body and "EnvironmentFile" in body and "WorkingDirectory" in body


def test_role_controls_dev_tool_surface():
    """researcher（给同事用）不给改代码/落分支/开 PR 的工具面；owner 才给。"""
    assert R.Assistant("a", "dingtalk", "/w", "/e", role="researcher").with_dev is False
    assert R.Assistant("a", "dingtalk", "/w", "/e", role="owner").with_dev is True


def test_systemd_hint_is_actionable(tmp_path):
    a = R.Assistant("alice", "dingtalk", "/opt/ws/alice", "/etc/v/alice.env")
    hint = R.systemd_hint(a)
    assert "systemctl enable --now vortocode-assistant@alice.service" in hint
    assert "/opt/ws/alice" in hint and "journalctl" in hint


def test_env_files_are_never_world_readable_in_examples():
    """文档里给的安装步骤必须自带 600——照抄就是安全的，别指望人事后想起来。"""
    tpl = Path(__file__).resolve().parents[2] / "examples" / "systemd" \
        / "vortocode-assistant@.service.example"
    body = tpl.read_text(encoding="utf-8")
    assert "-m 600" in body, "安装步骤没让 env 文件用 600"


def test_stat_helper_reads_mode(tmp_path):
    """夹具自身的健全性检查：chmod 真的生效了（不然权限那条测试是空转的）。"""
    p = _env(tmp_path / "x.env", mode=0o600)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
