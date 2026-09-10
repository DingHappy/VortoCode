"""定时推进：到点自己走，卡住了叫人——而且**只叫一次**。

这是把流水线从"人盯着才动"接到无人值守上的那一层。它的三条要害都不在"能不能推进"上
（那是 `advance` 的事，另有测试），而在**它对人说了什么**：

* 半小时一次的 cron，一条挂了三天的等批不能推 144 遍；
* 同一道工序被驳回重做后再次等批，那是新的一次，必须再说；
* 对外工序在无人值守下永远做不成——那就别去试，更别把重试次数烧光把自己废掉。
"""
import pytest
import yaml

from src.gateway.pipeline import MAX_STAGE_ATTEMPTS, PipelineStore, review
from src.gateway.pipeline_tick import MAX_RUNS_PER_TICK, tick

DEF = {
    "name": "content-ops",
    "stages": [
        {"id": "scout", "produces": "topic_pool", "review": True},
        {"id": "publish", "inputs": ["topic_pool"], "produces": "publication", "outbound": True},
    ],
}


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / ".vortocode" / "pipelines"
    d.mkdir(parents=True)
    (d / "content-ops.yaml").write_text(yaml.safe_dump(DEF, allow_unicode=True), encoding="utf-8")
    return str(tmp_path)


class Recorder:
    """假投递器。记下每条通知，好断"说了几次、说了什么"。"""

    def __init__(self):
        self.sent = []

    async def __call__(self, text, *, source="", trigger=""):
        self.sent.append((text, source))


async def _ok(stage_def, inputs):
    return {"payload": {"topics": ["A"]}, "summary": "3 个候选选题", "tokens": 7}


async def _boom(stage_def, inputs):
    raise RuntimeError("relay 502")


def _start(repo) -> str:
    from src.gateway.pipeline import load_definition
    return PipelineStore(repo).start(load_definition(repo, "content-ops")).run_id


# ------------------------------------------------------------------ 只在状态变化时说话
@pytest.mark.asyncio
async def test_the_same_waiting_state_is_announced_once(repo):
    """**这条是本模块存在的首要理由**：半小时一次的 cron，挂三天的等批会推 144 遍。"""
    _start(repo)
    notify = Recorder()
    for _ in range(3):
        await tick(repo, execute=_ok, notify=notify)
    assert len(notify.sent) == 1
    assert "等你批" in notify.sent[0][0]


@pytest.mark.asyncio
async def test_a_second_round_of_waiting_is_announced_again(repo):
    """驳回重做后再次等批是**新的一次**等批。签名清空的意义就在这儿——漏了它人就不知道该回来看。"""
    run_id = _start(repo)
    notify = Recorder()
    await tick(repo, execute=_ok, notify=notify)
    review(repo, run_id, verdict="reject", comment="角度太窄", reviewer="test")
    await tick(repo, execute=_ok, notify=notify)
    assert len(notify.sent) == 2


@pytest.mark.asyncio
async def test_notification_carries_the_next_step(repo):
    """只说"卡住了"的通知等于让人再查一遍。产出物摘要 + 怎么批，都要在这一条里。"""
    run_id = _start(repo)
    notify = Recorder()
    await tick(repo, execute=_ok, notify=notify)
    text, source = notify.sent[0]
    assert "3 个候选选题" in text and run_id in text
    assert "--approve" in text and "--reject" in text
    assert source == "pipeline:content-ops"          # 台账上"哪条流水线出的"要一眼看得出


@pytest.mark.asyncio
async def test_external_provenance_is_visible_in_the_notification(repo):
    """带外部来源的产出物，人在手机上就该看见标记——不能等他去终端 show 才知道。"""
    async def tainted(stage_def, inputs):
        return {"payload": {}, "summary": "5 个热点", "tainted": True,
                "taint_reason": "来自 web_search"}

    _start(repo)
    notify = Recorder()
    await tick(repo, execute=tainted, notify=notify)
    assert "⚠外部来源" in notify.sent[0][0]


# ------------------------------------------------------------------ 对外工序：不试也不烧
@pytest.mark.asyncio
async def test_outbound_is_not_attempted_without_a_confirm_channel(repo):
    """无人值守没有确认通道，outbound 必然 fail-closed。照常调 advance 的话，三个夜里
    就把 MAX_STAGE_ATTEMPTS 烧光，早上你想推时它已经永久卡死——**明知做不到还试三次，
    把自己废掉**。"""
    run_id = _start(repo)
    notify = Recorder()
    await tick(repo, execute=_ok, notify=notify)
    review(repo, run_id, verdict="approve", reviewer="test")

    for _ in range(MAX_STAGE_ATTEMPTS + 2):
        outcomes = await tick(repo, execute=_ok, notify=notify)

    assert outcomes[0].status == "needs_auth" and outcomes[0].blocked_on == "publish"
    assert PipelineStore(repo).load(run_id).stage("publish").attempts == 0   # 一次都没烧
    assert sum("🔒" in t for t, _ in notify.sent) == 1                        # 也只说一次
    assert "--yes" in next(t for t, _ in notify.sent if "🔒" in t)


@pytest.mark.asyncio
async def test_outbound_runs_when_a_confirm_channel_exists(repo):
    """有人授权就照常跑完——"不试"只针对没有确认通道那种情形，不是把对外工序永久禁掉。"""
    run_id = _start(repo)
    notify = Recorder()

    async def yes(_msg):
        return True

    await tick(repo, execute=_ok, notify=notify)
    review(repo, run_id, verdict="approve", reviewer="test")
    outcomes = await tick(repo, execute=_ok, notify=notify, confirm=yes)
    assert outcomes[0].status == "done"
    assert "✅" in notify.sent[-1][0]


# ------------------------------------------------------------------ 失败要说，且说清第几次
@pytest.mark.asyncio
async def test_failure_is_announced_with_the_attempt_count(repo):
    """试了一次没过和试满次数停下，是两条不同的消息——签名带上第几次，两条都送得出去。"""
    _start(repo)
    notify = Recorder()
    await tick(repo, execute=_boom, notify=notify)
    await tick(repo, execute=_boom, notify=notify)
    fails = [t for t, _ in notify.sent if "⛔" in t]
    assert len(fails) == 2 and "relay 502" in fails[0]


@pytest.mark.asyncio
async def test_a_stuck_run_stops_repeating_itself(repo):
    """重试次数耗尽后每次 tick 都会算出同一个签名 → 不再重复通报。"""
    _start(repo)
    notify = Recorder()
    for _ in range(MAX_STAGE_ATTEMPTS + 3):
        await tick(repo, execute=_boom, notify=notify)
    assert sum("⛔" in t for t, _ in notify.sent) == MAX_STAGE_ATTEMPTS


# ------------------------------------------------------------------ 扫描范围与上限
@pytest.mark.asyncio
async def test_terminal_runs_are_left_alone(repo):
    run_id = _start(repo)
    store = PipelineStore(repo)
    run = store.load(run_id)
    run.status = "abandoned"
    store.save(run)
    assert await tick(repo, execute=_ok, notify=Recorder()) == []


@pytest.mark.asyncio
async def test_runs_beyond_the_cap_are_reported_not_silently_dropped(repo):
    """每道工序都是一次真 LLM 回合，一次全推等于一次不受控的预算支出。超出的**如实标出来**，
    别在日志里假装处理过了。"""
    for _ in range(MAX_RUNS_PER_TICK + 2):
        _start(repo)
    outcomes = await tick(repo, execute=_ok, notify=Recorder())
    skipped = [o for o in outcomes if o.skipped]
    assert len(outcomes) == MAX_RUNS_PER_TICK + 2 and len(skipped) == 2
    assert all("下一轮" in o.reason for o in skipped)
    assert sum(bool(o.ran) for o in outcomes) == MAX_RUNS_PER_TICK


@pytest.mark.asyncio
async def test_pipeline_filter_narrows_the_scan(repo):
    _start(repo)
    assert await tick(repo, execute=_ok, notify=Recorder(), pipeline="别的线") == []


# ------------------------------------------------------------------ 投递器出问题不该反噬
@pytest.mark.asyncio
async def test_a_notifier_without_source_still_works(repo):
    """探测一次而不是靠异常试探（与 cron._accepts_attribution 同款）。"""
    got = []

    async def plain(text):                    # 老式投递器：不吃 source
        got.append(text)

    _start(repo)
    await tick(repo, execute=_ok, notify=plain)
    assert len(got) == 1


@pytest.mark.asyncio
async def test_advancing_still_happens_without_any_notifier(repo):
    """不给投递器就只推进不通报——但**推进本身照常发生**，别把通知做成推进的前提。"""
    run_id = _start(repo)
    await tick(repo, execute=_ok, notify=None)
    assert PipelineStore(repo).load(run_id).status == "awaiting_review"


# ------------------------------------------------------------------ 退出码（与 advance 刻意相反）
@pytest.mark.asyncio
async def test_tick_exits_zero_even_when_a_human_is_needed(repo, monkeypatch, capsys):
    """**等人批也退 0，是故意的。** 该说的话已经从通知通道说过了；cron 再按非零退出码报一次红，
    人会收到两条说同一件事的消息，run 台账里还会多一条不是失败的失败。
    （对照 `advance` 的 1=需要人处理——两个命令服务的读者不同。）
    """
    from src.gateway import pipeline_cli, pipeline_tick

    monkeypatch.chdir(repo)
    sent = []

    async def fake_tick(repo_root, **kw):
        assert kw.get("confirm") is None      # 无人值守入口不该凭空造出授权
        sent.append(kw)
        return [pipeline_tick.TickOutcome(run_id="prun-x", status="awaiting_review",
                                          blocked_on="scout", announced=True)]

    monkeypatch.setattr(pipeline_cli, "tick", fake_tick, raising=False)
    monkeypatch.setattr(pipeline_tick, "tick", fake_tick)
    assert await pipeline_cli.run_pipeline_cli("tick") == 0
    assert "已通报" in capsys.readouterr().out
