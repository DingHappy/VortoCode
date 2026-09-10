"""在手机上批流水线：/pipe 看、/ok 批、/no 驳、/later 挂。

通知里那句 `vc pipeline review prun-content-ops-ed3c577a --approve` 在手机上是没法用的——
让人抄一串 hex 是折磨。这层要的是"看一眼、回一句、它就往下走"，所以测的重点是：

* **run id 能整个省掉**（只有一条在等你批时），但**绝不替人挑**（多于一条就列出来）；
* 判定确定性地在桥里做，**不经过模型**——批不批是人的意思，让模型理解一句话再决定，
  等于把人闸换成一次概率事件；
* 批完**立刻往下推一道**。不推的话你点了"批"要等下一次 cron（两小时）才有动静，
  那不叫联动，那叫留言板。
"""
import asyncio

import pytest
import yaml

from src.gateway.pipeline import PipelineStore, load_definition
from src.gateway.products import ProductStore
from src.im.bridge import IMBridge
from tests.unit.test_im_bridge import OWNER, FakeAdapter, ScriptedLLM, _drive_no_turn, _msg

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
    return tmp_path


def _bridge(repo, adapter=None):
    adapter = adapter or FakeAdapter()
    return IMBridge(str(repo), adapter, OWNER, channel="test",
                    llm=ScriptedLLM("模型不该在人批这条路上出场")), adapter


def _waiting_run(repo, *, summary="3 个候选选题", tainted=False):
    """造一条停在 scout 闸门上的运行（连产出物一起），不跑任何 LLM。"""
    store = PipelineStore(str(repo))
    run = store.start(load_definition(str(repo), "content-ops"))
    product = ProductStore(str(repo)).create(
        "topic_pool", payload={"topics": ["A"]}, summary=summary,
        pipeline=run.pipeline, run_id=run.run_id, stage="scout", tainted=tainted,
        taint_reason="来自 web_search" if tainted else "")
    stage = run.stage("scout")
    stage.status, stage.product_id, stage.attempts = "awaiting_review", product.id, 1
    run.status = "awaiting_review"
    store.save(run)
    return run


async def _drive_with_advance(bridge, adapter, *events, timeout=5):
    """push 事件 → 若起了推进任务就等它跑完 → 收尾。"""
    run_task = asyncio.create_task(bridge.run())
    for e in events:
        adapter.push(e)
    for _ in range(200):                      # 让事件循环把命令处理完（可能起任务，也可能不起）
        await asyncio.sleep(0)
    if bridge._turn_task is not None:
        await asyncio.wait_for(bridge._turn_task, timeout=timeout)
    adapter.stop()
    await asyncio.wait_for(run_task, timeout=timeout)


def _no_real_advance(monkeypatch):
    """把真推进换成记账的假货——本文件测的是人批这一路，不是 advance（那边另有测试）。"""
    from src.gateway import pipeline as _p
    called = []

    async def fake(repo_root, run_id, **kw):
        called.append((run_id, kw))
        return _p.AdvanceResult(run_id=run_id, status="done", ran=["publish"],
                                reason="全部工序完成")

    monkeypatch.setattr(_p, "advance", fake)
    return called


# ------------------------------------------------------------------ 看
@pytest.mark.asyncio
async def test_pipe_lists_waiting_runs_first(repo):
    """手机上第一屏就该是"该你管的"。"""
    _waiting_run(repo)
    running = PipelineStore(str(repo)).start(load_definition(str(repo), "content-ops"))
    bridge, adapter = _bridge(repo)
    await _drive_no_turn(bridge, adapter, _msg("/pipe"))
    out = adapter.texts()[-1]
    assert out.index("📋") < out.index(running.run_id)


@pytest.mark.asyncio
async def test_detail_shows_the_product_and_its_provenance(repo):
    """批之前该看见的就在这一屏：产出物摘要 + 外部来源标记。"""
    run = _waiting_run(repo, summary="5 个热点选题", tainted=True)
    bridge, adapter = _bridge(repo)
    await _drive_no_turn(bridge, adapter, _msg(f"/pipe {run.run_id}"))
    out = adapter.texts()[-1]
    assert "5 个热点选题" in out and "⚠外部来源" in out


# ------------------------------------------------------------------ 批
@pytest.mark.asyncio
async def test_approve_needs_no_run_id_when_only_one_waits(repo, monkeypatch):
    """**手机上让人抄 prun-content-ops-ed3c577a 是折磨。** 只有一条在等就是它。"""
    run = _waiting_run(repo)
    called = _no_real_advance(monkeypatch)
    bridge, adapter = _bridge(repo)
    await _drive_with_advance(bridge, adapter, _msg("/ok"))
    assert PipelineStore(str(repo)).load(run.run_id).stage("scout").status == "done"
    assert called and called[0][0] == run.run_id       # 批完立刻往下推，不等 cron


@pytest.mark.asyncio
async def test_approve_never_picks_for_you_when_several_wait(repo, monkeypatch):
    """多于一条在等就把候选列出来。**替人挑一个是最不该做的事**——批错了没有撤销键。"""
    a, b = _waiting_run(repo), _waiting_run(repo)
    _no_real_advance(monkeypatch)
    bridge, adapter = _bridge(repo)
    await _drive_with_advance(bridge, adapter, _msg("/ok"))
    out = adapter.texts()[-1]
    assert a.run_id in out and b.run_id in out
    store = PipelineStore(str(repo))
    assert all(store.load(r.run_id).status == "awaiting_review" for r in (a, b))


@pytest.mark.asyncio
async def test_a_named_run_that_cannot_be_found_is_an_error_not_a_fallback(repo, monkeypatch):
    """`/ok prun-打错了` 必须报错。转头去批那条唯一在等的——用户指了名系统却批了别的，
    比报错难查得多。"""
    run = _waiting_run(repo)
    _no_real_advance(monkeypatch)
    bridge, adapter = _bridge(repo)
    await _drive_with_advance(bridge, adapter, _msg("/ok prun-打错了"))
    assert "没找到" in adapter.texts()[-1]
    assert PipelineStore(str(repo)).load(run.run_id).status == "awaiting_review"


@pytest.mark.asyncio
async def test_chinese_alias_does_the_same_thing(repo, monkeypatch):
    """中英两套拼写指同一件事：ASCII 那套能被 Telegram 补全认出，中文那套手上快。"""
    run = _waiting_run(repo)
    _no_real_advance(monkeypatch)
    bridge, adapter = _bridge(repo)
    await _drive_with_advance(bridge, adapter, _msg("/批"))
    assert PipelineStore(str(repo)).load(run.run_id).stage("scout").status == "done"


# ------------------------------------------------------------------ 驳
@pytest.mark.asyncio
async def test_reject_without_a_reason_is_refused(repo):
    """不说为什么，重跑出来还是原样——那一趟 token 白烧。"""
    run = _waiting_run(repo)
    bridge, adapter = _bridge(repo)
    await _drive_no_turn(bridge, adapter, _msg("/no"))
    assert "为什么" in adapter.texts()[-1]
    assert PipelineStore(str(repo)).load(run.run_id).status == "awaiting_review"


@pytest.mark.asyncio
async def test_the_comment_is_not_mistaken_for_a_run_id(repo, monkeypatch):
    """`/no 角度太窄` 里的"角度太窄"是意见，不该被当成 id 去查。意见要落进血缘。"""
    run = _waiting_run(repo)
    _no_real_advance(monkeypatch)
    bridge, adapter = _bridge(repo)
    await _drive_with_advance(bridge, adapter, _msg("/no 角度太窄，换个切入点"))
    note = ProductStore(str(repo)).latest("review_note")
    assert note.payload["comment"] == "角度太窄，换个切入点"
    assert note.payload["reviewer"] == "im"
    assert PipelineStore(str(repo)).load(run.run_id).stage("scout").status == "pending"


@pytest.mark.asyncio
async def test_defer_leaves_it_waiting_and_does_not_advance(repo, monkeypatch):
    """挂起=不推进也不失败。原地等着，别顺手往下跑。"""
    run = _waiting_run(repo)
    called = _no_real_advance(monkeypatch)
    bridge, adapter = _bridge(repo)
    await _drive_with_advance(bridge, adapter, _msg("/later 这周先不发"))
    assert PipelineStore(str(repo)).load(run.run_id).status == "awaiting_review"
    assert not called


# ------------------------------------------------------------------ 边界
@pytest.mark.asyncio
async def test_nothing_waiting_says_so_plainly(repo):
    PipelineStore(str(repo)).start(load_definition(str(repo), "content-ops"))
    bridge, adapter = _bridge(repo)
    await _drive_no_turn(bridge, adapter, _msg("/ok"))
    assert "没有等你批" in adapter.texts()[-1]


@pytest.mark.asyncio
async def test_a_stranger_cannot_approve(repo):
    """人批是**人**的意思。白名单之外的人连命令都不该被解析。"""
    run = _waiting_run(repo)
    bridge, adapter = _bridge(repo)
    await _drive_no_turn(bridge, adapter, _msg("/ok", sender="stranger-9"))
    assert PipelineStore(str(repo)).load(run.run_id).status == "awaiting_review"
    assert bridge._ignored == 1


@pytest.mark.asyncio
async def test_approving_while_busy_does_not_steal_the_task_slot(repo, monkeypatch):
    """有活在跑就别抢任务位——回执照发，推进等下一次（cron 会接上）。"""
    run = _waiting_run(repo)
    called = _no_real_advance(monkeypatch)
    bridge, adapter = _bridge(repo)

    async def _busy():
        await asyncio.sleep(0.2)

    bridge._turn_task = asyncio.create_task(_busy())
    await bridge._handle_command("/ok")
    assert PipelineStore(str(repo)).load(run.run_id).stage("scout").status == "done"
    assert not called
    await bridge._turn_task


@pytest.mark.asyncio
async def test_outbound_stage_asks_on_your_phone_instead_of_refusing(repo, monkeypatch):
    """**这才是接到 IM 上的意义**：无人值守下对外工序 fail-closed 拒绝，而这里人就在关口——
    弹按钮问一句，你点了才发。"""
    run = _waiting_run(repo)
    stages = []

    from src.agents import pipeline_exec

    def fake_builder(repo_root, *, confirm=None, on_progress=None, **kw):
        async def execute(stage_def, inputs):
            stages.append(stage_def.id)
            assert confirm is not None, "IM 这条路必须给确认通道"
            if stage_def.outbound and not await confirm(f"要发布 {stage_def.id} 吗"):
                raise RuntimeError("未放行")
            return {"payload": {}, "summary": f"{stage_def.id} 完成"}
        return execute

    monkeypatch.setattr(pipeline_exec, "build_stage_executor", fake_builder)
    bridge, adapter = _bridge(repo, FakeAdapter(auto_approve=True))   # 模拟主人点了"批准"
    await _drive_with_advance(bridge, adapter, _msg("/ok"))

    assert stages == ["publish"]
    assert any(k == "confirm" for k, _ in adapter.sent)   # 真的在手机上问了
    assert PipelineStore(str(repo)).load(run.run_id).status == "done"


# ------------------------------------------------------------------ /go（推一道）
@pytest.mark.asyncio
async def test_go_pushes_a_stage_that_unattended_refuses_to_touch(repo, monkeypatch):
    """`/go` 存在的理由就是**无人值守推不动的那一类**。

    对外工序在 cron 里连试都不试（没有确认通道），通知说"这一步要你带授权来"——
    那句话在手机上得有个落点，否则人只能爬去终端。
    """
    run = _waiting_run(repo)
    store = PipelineStore(str(repo))
    approved = store.load(run.run_id)
    approved.stage("scout").status = "done"
    approved.status = "running"
    store.save(approved)

    called = _no_real_advance(monkeypatch)
    bridge, adapter = _bridge(repo)
    await _drive_with_advance(bridge, adapter, _msg("/go"))
    assert called and called[0][0] == run.run_id


@pytest.mark.asyncio
async def test_go_on_a_run_waiting_for_you_sends_you_back_to_the_gate(repo, monkeypatch):
    """停在闸门上的运行不该被 `/go` 绕过去——那就是把人闸做成了摆设。"""
    run = _waiting_run(repo)
    called = _no_real_advance(monkeypatch)
    bridge, adapter = _bridge(repo)
    await _drive_with_advance(bridge, adapter, _msg("/go"))
    assert "等你批" in adapter.texts()[-1] and not called
    assert PipelineStore(str(repo)).load(run.run_id).status == "awaiting_review"


@pytest.mark.asyncio
async def test_go_never_picks_among_several_running(repo, monkeypatch):
    a, b = (PipelineStore(str(repo)).start(load_definition(str(repo), "content-ops")),
            PipelineStore(str(repo)).start(load_definition(str(repo), "content-ops")))
    called = _no_real_advance(monkeypatch)
    bridge, adapter = _bridge(repo)
    await _drive_with_advance(bridge, adapter, _msg("/go"))
    out = adapter.texts()[-1]
    assert a.run_id in out and b.run_id in out and not called
