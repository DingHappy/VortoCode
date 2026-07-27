"""定时作业的观察 + 排班面（agent 侧）。

真机 2026-07-27：用户问"能不能设置定时任务"，agent 答"我目前没有设置定时任务的能力"，
接着推荐 crontab / GitHub Actions / **IFTTT、Zapier**——而 VortoCode 自己的 cron 子系统
当时就跑在那台机器上（`VORTOCODE_CRON=1`，relay_duty 每天 02:00）。就工具而言它没说谎
（确实一个 cron 工具都没有），但把主人推去用外部服务是实打实的错。

本文件钉四件事，每件都是"改坏了不会自己喊疼"的那种：
1. **无人值守不给排班工具** —— cron 作业能建 cron 作业 = 自我复制驻留。
2. **写面必须过确认门** —— 拒绝就是不生效，且落盘的是不会跑的停用态（fail-closed 方向）。
3. **不毁主人的文件** —— cron.yaml 里全是手写注释与别的作业，编辑只准追加/改单行。
4. **触发守卫与 REST 同源** —— 停用的不许触发、command 作业过宿主机执行闸。
"""

import pytest

from src.gateway.cron import (CronEditError, add_job, check_trigger, load_jobs, parse_jobs_text,
                              set_job_enabled)

# 一份"像真的"的作业表：带主人手写注释 + 一个已有作业。编辑面不许把这些弄丢。
SEED = """\
# VortoCode cron 作业表——主人手写的经验都在注释里，别给我抹了。
jobs:
  # 中转站巡检：确定性作业，零 LLM。绝对路径解释器是踩过坑才写死的（PATH 极简）。
  - name: relay_duty
    schedule: "at 02:00"
    command: "/opt/venv/bin/python -m src.gateway.relay_duty"
    timeout: 120
    announce: im
    enabled: true
"""


def _seed(tmp_path):
    d = tmp_path / ".vortocode"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cron.yaml").write_text(SEED, encoding="utf-8")
    return tmp_path


def _text(tmp_path):
    return (tmp_path / ".vortocode" / "cron.yaml").read_text(encoding="utf-8")


def _tools(tmp_path, confirm=None):
    from src.agents.main_agent import build_cron_tools
    return {t.name: t for t in build_cron_tools(str(tmp_path), confirm)}


def _yes():
    async def f(_m):
        return True
    return f


def _no():
    async def f(_m):
        return False
    return f


# ---------------------------------------------------------------- 1. 无人值守不给排班（自我复制闸）
async def test_unattended_session_cannot_schedule_itself(tmp_path):
    """cron 作业能创建 cron 作业 = 自我复制驻留。这条红了说明闸被拆了。"""
    from src.gateway.session import run_isolated_session

    captured = {}

    def _spy(*a, **kw):
        captured.update(kw)
        return []

    import src.agents.main_agent as ma
    real = ma.build_agent_tools
    ma.build_agent_tools = _spy
    try:
        await run_isolated_session(str(tmp_path), "随便干点啥")
    except Exception:  # noqa: BLE001 —— 空工具集下后续装配/跑动怎么炸都无所谓，只看入参
        pass
    finally:
        ma.build_agent_tools = real
    assert captured.get("with_cron") is False, "无人值守拿到了排班工具——自我复制闸被拆了"
    assert captured.get("with_web") is False, "顺带守住出网闸（同一条道理的空间版）"


def test_researcher_has_no_cron_tools(tmp_path):
    """研究员是给同事用的资料助理，排班属主人运维面。"""
    from src.gateway.agent_session import build_session

    names = set(build_session(str(tmp_path), kind="im", confirm=None, with_dev=False).tools)
    assert not [n for n in names if n.startswith("cron_")], names


def test_owner_ends_do_have_cron_tools(tmp_path):
    from src.gateway.agent_session import build_session

    names = set(build_session(str(tmp_path), kind="im", confirm=None).tools)
    assert {"cron_list", "cron_add", "cron_toggle", "cron_run"} <= names


# ---------------------------------------------------------------- 2. 写面过确认门
async def test_add_lands_disabled_when_owner_declines(tmp_path):
    """拒绝启用 → 作业留在表里但**不会跑**。fail-closed 的方向是"不跑"，不是"不写"。"""
    _seed(tmp_path)
    out = await _tools(tmp_path, _no())["cron_add"].handler(
        {"name": "daily_news", "schedule": "at 09:00", "prompt": "搜今天的新闻给我"})
    job = {j.name: j for j in load_jobs(tmp_path)}["daily_news"]
    assert job.enabled is False and "停用" in out


async def test_add_enables_only_after_approval(tmp_path):
    _seed(tmp_path)
    out = await _tools(tmp_path, _yes())["cron_add"].handler(
        {"name": "daily_news", "schedule": "at 09:00", "prompt": "搜今天的新闻给我"})
    job = {j.name: j for j in load_jobs(tmp_path)}["daily_news"]
    assert job.enabled is True and job.kind == "prompt" and "✅" in out


async def test_no_confirm_callback_means_never_enabled(tmp_path):
    """端没接确认回调（fail-closed）→ 绝不该有作业自己变成启用态。"""
    _seed(tmp_path)
    await _tools(tmp_path, None)["cron_add"].handler(
        {"name": "x", "schedule": "every 5m", "prompt": "干活"})
    assert {j.name: j for j in load_jobs(tmp_path)}["x"].enabled is False


async def test_toggle_enable_asks_but_disable_does_not(tmp_path):
    """启用要问（扩大自动执行面）；停用不问——让人更容易关掉一个正在捣乱的作业。"""
    _seed(tmp_path)
    t = _tools(tmp_path, _no())["cron_toggle"]
    assert "未启用" in await t.handler({"name": "relay_duty", "enabled": True})
    assert load_jobs(tmp_path)[0].enabled is True                  # 拒绝 → 原样

    assert "✅" in await t.handler({"name": "relay_duty", "enabled": False})
    assert load_jobs(tmp_path)[0].enabled is False                 # 停用不需要点头


# ---------------------------------------------------------------- 3. 不毁主人的文件
def test_add_preserves_comments_and_siblings(tmp_path):
    _seed(tmp_path)
    add_job(tmp_path, name="newjob", schedule="every 6h", prompt="看看有什么要做的")
    text = _text(tmp_path)
    assert "主人手写的经验都在注释里" in text, "整表重写把顶部注释抹了"
    assert "绝对路径解释器是踩过坑才写死的" in text, "把作业内注释抹了"
    names = {j.name for j in load_jobs(tmp_path)}
    assert names == {"relay_duty", "newjob"}
    assert {j.name: j for j in load_jobs(tmp_path)}["relay_duty"].command.endswith("relay_duty")


def test_toggle_only_touches_one_line(tmp_path):
    _seed(tmp_path)
    before = _text(tmp_path).splitlines()
    set_job_enabled(tmp_path, "relay_duty", False)
    after = _text(tmp_path).splitlines()
    diff = [(a, b) for a, b in zip(before, after) if a != b]
    assert len(before) == len(after) and len(diff) == 1, f"改动溢出到别的行：{diff}"
    assert "enabled" in diff[0][0]


def test_multiline_prompt_survives_round_trip(tmp_path):
    """prompt 带换行/引号/冒号是常态——手拼字符串必出转义洞，这里钉住必须走 yaml 转义。"""
    _seed(tmp_path)
    nasty = '第一行: 有冒号\n第二行 "有引号"\n- 还有个横杠开头'
    add_job(tmp_path, name="tricky", schedule="every 1h", prompt=nasty)
    got = {j.name: j for j in load_jobs(tmp_path)}
    assert got["tricky"].prompt == nasty
    assert "relay_duty" in got, "转义没做好，把别的作业挤坏了"


def test_prompt_cannot_inject_extra_yaml_keys(tmp_path):
    """把 command: 藏进 prompt 文本里，不该变成一个真的 command 作业（那就绕过了只建 prompt 的边界）。"""
    _seed(tmp_path)
    add_job(tmp_path, name="inj", schedule="every 1h",
            prompt='正常内容\n  command: "rm -rf /"\n  enabled: true')
    job = {j.name: j for j in load_jobs(tmp_path)}["inj"]
    assert job.kind == "prompt" and job.command == ""


def test_creates_file_when_absent(tmp_path):
    add_job(tmp_path, name="first", schedule="at 08:00", prompt="早报")
    assert {j.name for j in load_jobs(tmp_path)} == {"first"}


# ---------------------------------------------------------------- 入参校验（先校验再烦人）
@pytest.mark.parametrize("kw, needle", [
    ({"name": "bad name", "schedule": "at 09:00", "prompt": "x"}, "作业名非法"),
    ({"name": "../escape", "schedule": "at 09:00", "prompt": "x"}, "作业名非法"),
    ({"name": "ok", "schedule": "每天早上", "prompt": "x"}, "schedule 非法"),
    ({"name": "ok", "schedule": "at 99:99", "prompt": "x"}, "schedule 非法"),
    ({"name": "ok", "schedule": "at 09:00", "prompt": "  "}, "prompt 不能为空"),
    ({"name": "relay_duty", "schedule": "at 09:00", "prompt": "x"}, "已存在"),
])
def test_rejects_bad_input_without_touching_the_file(tmp_path, kw, needle):
    _seed(tmp_path)
    with pytest.raises(CronEditError) as e:
        add_job(tmp_path, **kw)
    assert needle in str(e.value)
    assert _text(tmp_path) == SEED, "被拒的请求不该动文件"


def test_add_refuses_to_write_a_command_job(tmp_path):
    """只建 prompt 作业——command 是无人值守的周期性任意 shell，人在确认框里也难看清后果。"""
    from src.agents.main_agent import build_cron_tools
    args = {t.name: set(t.args) for t in build_cron_tools(str(tmp_path))}
    assert "command" not in args["cron_add"], "cron_add 暴露了 command 参数"


def test_no_delete_tool_exists(tmp_path):
    """刻意不给删除：停用可逆可见，误删主人的巡检作业不可逆。"""
    names = set(_tools(tmp_path))
    assert not any(k in n for n in names for k in ("delete", "remove", "rm")), names


# ---------------------------------------------------------------- 4. 触发守卫（与 REST 同源）
def test_disabled_job_cannot_be_triggered(tmp_path):
    _seed(tmp_path)
    set_job_enabled(tmp_path, "relay_duty", False)
    reason, code, _ = check_trigger(str(tmp_path), "relay_duty")
    assert code == "disabled" and "已停用" in reason


def test_unknown_job_is_not_found(tmp_path):
    _seed(tmp_path)
    assert check_trigger(str(tmp_path), "nope")[1] == "not_found"


def test_command_job_needs_the_host_execution_switch(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.delenv("VORTOCODE_ENABLE_SHELL", raising=False)
    assert check_trigger(str(tmp_path), "relay_duty")[1] == "shell_disabled"
    monkeypatch.setenv("VORTOCODE_ENABLE_SHELL", "1")
    assert check_trigger(str(tmp_path), "relay_duty")[1] is None


def test_same_job_cannot_be_triggered_concurrently(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setenv("VORTOCODE_ENABLE_SHELL", "1")
    from src.gateway.cron import track_trigger

    class _Running:
        def done(self):
            return False

        def add_done_callback(self, _cb):
            pass

    track_trigger("relay_duty", _Running())
    try:
        assert check_trigger(str(tmp_path), "relay_duty")[1] == "busy"
    finally:
        from src.gateway.cron import _TRIGGER_INFLIGHT
        _TRIGGER_INFLIGHT.pop("relay_duty", None)


async def test_run_declined_does_not_start_anything(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setenv("VORTOCODE_ENABLE_SHELL", "1")
    started = []
    import src.gateway.cron as cron
    monkeypatch.setattr(cron, "run_job_by_name", lambda *a, **k: started.append(1))
    out = await _tools(tmp_path, _no())["cron_run"].handler({"name": "relay_duty"})
    assert "未触发" in out and not started


# ---------------------------------------------------------------- 观察面
async def test_list_reports_schedule_switch_and_content(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setenv("VORTOCODE_CRON", "1")
    out = await _tools(tmp_path)["cron_list"].handler({})
    assert "relay_duty" in out and "at 02:00" in out and "✅ 开" in out

    monkeypatch.setenv("VORTOCODE_CRON", "0")
    out = await _tools(tmp_path)["cron_list"].handler({})
    assert "关" in out, "调度器没开却不说，用户会以为作业在跑"


async def test_list_on_empty_table_points_at_the_switch(tmp_path):
    out = await _tools(tmp_path)["cron_list"].handler({})
    assert "还没有任何作业" in out and "VORTOCODE_CRON=1" in out


def test_cron_list_is_read_only(tmp_path):
    assert _tools(tmp_path)["cron_list"].read_only is True
    for name in ("cron_add", "cron_toggle", "cron_run"):
        assert _tools(tmp_path)[name].read_only is False, f"{name} 被标成只读会绕开 plan/build 门"


# ---------------------------------------------------------------- 落盘前自检
def test_verification_uses_the_real_parser(tmp_path):
    """自检必须用真解析（parse_jobs_text），而不是另写一套"应该也一样"的校验。"""
    _seed(tmp_path)
    add_job(tmp_path, name="probe", schedule="every 2h", prompt="p")
    assert {j.name for j in parse_jobs_text(_text(tmp_path))} == {"relay_duty", "probe"}


def test_toggle_on_missing_job_leaves_file_untouched(tmp_path):
    _seed(tmp_path)
    with pytest.raises(CronEditError):
        set_job_enabled(tmp_path, "ghost", True)
    assert _text(tmp_path) == SEED




# ---------------------------------------------------------------- 5. 作业级出网许可（2026-07-27 主人拍板）
#
# 无人值守整档不出网，是因为 web_fetch 是 read_only、不过确认门，而 GET 的 query string 就是
# 一条外传通道。但"每天搜新闻"这类作业确实要出网——真机首跑就如实报告了"我没有 web_search"。
# 于是把边界从**档级**细化到**作业级**：谁要出网谁单独申报，且申报那一刻有真人点头。

async def test_web_permission_is_asked_separately_from_enabling(tmp_path):
    """两件事的风险不是一个量级，合成一句话 = 让人在不知情下顺手交出无人值守的外传通道。"""
    _seed(tmp_path)
    asked = []

    async def _spy(msg):
        asked.append(str(msg))
        return True

    await _tools(tmp_path, _spy)["cron_add"].handler(
        {"name": "news", "schedule": "at 09:00", "prompt": "搜新闻", "allow_web": True})
    assert len(asked) == 2, f"出网许可没有单独问：{asked}"
    assert "出网许可" in asked[0] and "外带通道" in asked[0], "许可问句没讲清风险"
    assert "启用" in asked[1]


async def test_declining_web_still_creates_the_job_without_web(tmp_path):
    """许可被拒 → 按不出网建（作业仍有用），而不是整个作业不建。"""
    _seed(tmp_path)
    answers = iter([False, True])                  # 拒绝出网、同意启用

    async def _mixed(_msg):
        return next(answers)

    out = await _tools(tmp_path, _mixed)["cron_add"].handler(
        {"name": "news", "schedule": "at 09:00", "prompt": "搜新闻", "allow_web": True})
    job = {j.name: j for j in load_jobs(tmp_path)}["news"]
    assert job.enabled is True and job.allow_web is False
    assert "不能联网" in out, "没告诉用户这作业上不了网，到点才发现就晚了"


async def test_web_permission_not_asked_when_not_requested(tmp_path):
    """没申报就别问——多余的安全问句会训练用户闭眼点同意。"""
    _seed(tmp_path)
    asked = []

    async def _spy(msg):
        asked.append(str(msg))
        return True

    await _tools(tmp_path, _spy)["cron_add"].handler(
        {"name": "local", "schedule": "at 09:00", "prompt": "跑测试"})
    assert len(asked) == 1 and "出网许可" not in asked[0]
    assert {j.name: j for j in load_jobs(tmp_path)}["local"].allow_web is False


async def test_granted_web_lands_in_yaml_and_shows_in_list(tmp_path):
    _seed(tmp_path)
    await _tools(tmp_path, _yes())["cron_add"].handler(
        {"name": "news", "schedule": "at 09:00", "prompt": "搜新闻", "allow_web": True})
    assert {j.name: j for j in load_jobs(tmp_path)}["news"].allow_web is True
    assert "allow_web: true" in _text(tmp_path)

    listing = await _tools(tmp_path)["cron_list"].handler({})
    assert "🌐可联网" in listing, "清单看不出哪个作业能出网，主人无法审计"
    assert "relay_duty" in listing and listing.count("🌐") == 1, "把不能出网的也标了"


def test_existing_jobs_default_to_no_web(tmp_path):
    """老作业表没有这个字段——默认必须是不出网，不能因为新增字段就悄悄放开。"""
    _seed(tmp_path)
    assert all(j.allow_web is False for j in load_jobs(tmp_path))


async def test_unattended_session_web_follows_the_job_flag(tmp_path):
    """真正决定出不出网的是 run_isolated_session 的入参，不是文案。"""
    import src.agents.main_agent as ma
    from src.gateway.session import run_isolated_session

    seen = {}

    def _spy(*a, **kw):
        seen.update(kw)
        return []

    real = ma.build_agent_tools
    ma.build_agent_tools = _spy
    try:
        for flag in (False, True):
            seen.clear()
            try:
                await run_isolated_session(str(tmp_path), "干活", allow_web=flag)
            except Exception:  # noqa: BLE001 —— 空工具集后续怎么炸无所谓，只看入参
                pass
            assert seen.get("with_web") is flag
            assert seen.get("with_im_media") is False, "出站投递面不该随出网许可放开"
    finally:
        ma.build_agent_tools = real


async def test_job_carries_allow_web_into_the_run(tmp_path):
    """cron.run_job 必须把作业的 allow_web 透传给隔离会话，否则 yaml 里写了也白写。"""
    from src.gateway.cron import load_jobs as _lj
    from src.gateway.cron import run_job

    _seed(tmp_path)
    add_job(tmp_path, name="news", schedule="at 09:00", prompt="搜新闻", allow_web=True)
    job = {j.name: j for j in _lj(tmp_path)}["news"]
    got = {}

    async def _fake_session(root, prompt, **kw):
        got.update(kw)
        return "ok"

    await run_job(str(tmp_path), job, run_session=_fake_session)
    assert got.get("allow_web") is True


# ---------------------------------------------------------------- 6. 给已有作业开/关出网（设计修正）
#
# 起初 cron_add "只新增不改已有"，理由是"不给 agent 提权路径"。真机 2026-07-27 撞到：主人说
# 「给 daily-tech-news 加上联网权限」，agent 只能让他去手工编辑 yaml。
# 复盘发现**那条限制没有真正限制任何东西**——agent 本来就能用 cron_add 建一个 allow_web: true
# 的新作业（同样过确认门），可达的端状态完全一样。禁止修改只是把人逼去手工编辑，安全上一分
# 钱没买到。真正该守的是「**内容**不可改」：prompt/command/schedule 一律不许动，所以劫持不了
# relay_duty 这类已被信任的作业去干别的。

async def test_can_grant_web_to_an_existing_job(tmp_path):
    _seed(tmp_path)
    add_job(tmp_path, name="news", schedule="at 09:00", prompt="搜新闻")
    out = await _tools(tmp_path, _yes())["cron_set_web"].handler({"name": "news", "allow_web": True})
    assert "✅" in out and {j.name: j for j in load_jobs(tmp_path)}["news"].allow_web is True


async def test_granting_web_requires_explicit_consent(tmp_path):
    _seed(tmp_path)
    add_job(tmp_path, name="news", schedule="at 09:00", prompt="搜新闻")
    asked = []

    async def _spy(msg):
        asked.append(str(msg))
        return False

    out = await _tools(tmp_path, _spy)["cron_set_web"].handler({"name": "news", "allow_web": True})
    assert "拒绝" in out and asked and "外带通道" in asked[0], "没讲清风险就问了"
    assert "搜新闻" in asked[0], "没告诉主人这作业到点会干什么"
    assert {j.name: j for j in load_jobs(tmp_path)}["news"].allow_web is False


async def test_revoking_web_needs_no_consent(tmp_path):
    """收权是缩小面——别为了仪式感拦着人关掉一个正在联网的作业。"""
    _seed(tmp_path)
    add_job(tmp_path, name="news", schedule="at 09:00", prompt="搜新闻", allow_web=True)
    out = await _tools(tmp_path, _no())["cron_set_web"].handler({"name": "news", "allow_web": False})
    assert "✅" in out and {j.name: j for j in load_jobs(tmp_path)}["news"].allow_web is False


async def test_no_op_when_already_in_that_state(tmp_path):
    """已经是目标状态就别问——多余的安全问句会训练人闭眼点同意。"""
    _seed(tmp_path)
    add_job(tmp_path, name="news", schedule="at 09:00", prompt="搜新闻", allow_web=True)
    asked = []

    async def _spy(msg):
        asked.append(str(msg))
        return True

    out = await _tools(tmp_path, _spy)["cron_set_web"].handler({"name": "news", "allow_web": True})
    assert "无需改动" in out and not asked


async def test_unknown_job_is_reported_not_created(tmp_path):
    _seed(tmp_path)
    before = {j.name for j in load_jobs(tmp_path)}
    out = await _tools(tmp_path, _yes())["cron_set_web"].handler({"name": "ghost", "allow_web": True})
    assert "无此作业" in out
    assert {j.name for j in load_jobs(tmp_path)} == before, "改不存在的作业不该凭空造一个出来"


def test_web_toggle_never_touches_job_content(tmp_path):
    """守的是这条：不许改 prompt/command/schedule，所以劫持不了已被信任的作业。"""
    from src.gateway.cron import set_job_web

    _seed(tmp_path)
    before = {j.name: (j.schedule.raw, j.prompt, j.command) for j in load_jobs(tmp_path)}
    set_job_web(tmp_path, "relay_duty", True)
    after = {j.name: (j.schedule.raw, j.prompt, j.command) for j in load_jobs(tmp_path)}
    assert before == after, "改出网许可动到了作业内容"
    assert "主人手写的经验都在注释里" in _text(tmp_path)
    assert "绝对路径解释器是踩过坑才写死的" in _text(tmp_path)


def test_web_toggle_is_a_single_line_change(tmp_path):
    from src.gateway.cron import set_job_web

    _seed(tmp_path)
    before = _text(tmp_path).splitlines()
    set_job_web(tmp_path, "relay_duty", True)
    after = _text(tmp_path).splitlines()
    assert len(after) == len(before) + 1, "不是单行插入"
    assert "allow_web: true" in after[before.index("  - name: relay_duty") + 1]


def test_cron_tools_still_expose_no_content_editing(tmp_path):
    """能改开关，但**不能改内容**——没有任何工具接受 prompt/command/schedule 去改已有作业。"""
    tools = _tools(tmp_path)
    for name in ("cron_toggle", "cron_set_web", "cron_run"):
        args = set(tools[name].args)
        assert not (args & {"prompt", "command", "schedule"}), f"{name} 开了改内容的口子：{args}"


# ---------------------------------------------------------------- 7. 跑完要真的推到人手机上
#
# 真机 2026-07-27：主人从钉钉手动触发，作业跑成功了（台账里有完整新闻摘要），**但他什么都没收到**。
# 根因：`cron_run` 工具调 run_job_by_name 时没传 notify，于是 `_announce` 走 record_notice 兜底——
# 只写盘。而调度循环传了、REST 触发路由也传了，唯独我新加的这个工具漏了。
# 证据留在台账的 source 字段里：`scheduler`=走了三路投递（人收到了），`cron:<name>`=只落了盘。
#
# 这一组断的是「**人真的收到了**」，不是「某个函数被调了」——今天已经在"验了个不在链路上的
# 东西"上栽过一次（#252）。

async def test_manual_trigger_pushes_to_the_owner(tmp_path, monkeypatch):
    """从聊天里手动触发 → IM 那一路必须真的被推到。"""
    _seed(tmp_path)
    add_job(tmp_path, name="news", schedule="at 09:00", prompt="搜新闻")

    pushed = []
    import src.gateway.im_runtime as im_rt
    monkeypatch.setattr(im_rt, "notify_owner", lambda t: pushed.append(str(t)) or _noop())

    import src.gateway.cron as cron
    async def _fake_session(root, prompt, **kw):
        return "📰 今日科技新闻摘要：…"
    monkeypatch.setattr(cron, "run_isolated_session", _fake_session, raising=False)

    tool = _tools(tmp_path, _yes())["cron_run"]
    monkeypatch.setenv("VORTOCODE_ENABLE_SHELL", "1")
    await tool.handler({"name": "news"})
    task = cron._TRIGGER_INFLIGHT.get("news")
    if task is not None:
        await task
    assert pushed, "作业跑完了，人手机上什么都没收到（notify 没传）"
    assert "news" in pushed[0]


async def _noop():
    return None


def test_notifier_is_a_single_source(tmp_path):
    """三路投递器只此一份：web 路由的入口必须委派到 gateway，别各写一份（第三个调用方总会漏）。"""
    from src.gateway.notices import make_notifier as gw
    from src.web.routers.tasks import make_notifier as web

    a, b = gw(str(tmp_path)), web(str(tmp_path))
    assert callable(a) and callable(b)
    assert a.__qualname__ == b.__qualname__, "web 侧又自己实现了一份投递器"


async def test_notifier_writes_the_ledger_even_when_im_is_down(tmp_path, monkeypatch):
    """台账是唯一有持久保证的一路：IM 挂了也不能把结果丢了。"""
    from src.gateway.notices import load_notices, make_notifier

    import src.gateway.im_runtime as im_rt

    async def _boom(_t):
        raise RuntimeError("钉钉长连断了")

    monkeypatch.setattr(im_rt, "notify_owner", _boom)
    await make_notifier(str(tmp_path))("作业跑完了")
    assert any("作业跑完了" in n.get("text", "") for n in load_notices(str(tmp_path)))
