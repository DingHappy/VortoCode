"""作业流水线：幂等推进 + 人批三档 + 驳回重做。

断行为不断源码。执行器全部是注入的假的——本层测的是"推到哪了、谁在等谁、驳回之后回到哪"，
不测 LLM。
"""
import pytest
import yaml

from src.gateway.pipeline import (
    PipelineDef, PipelineStore, advance, load_definition, review, review_fingerprint,
)
from src.gateway.products import ProductStore

CONTENT_OPS = {
    "name": "content-ops",
    "stages": [
        {"id": "scout", "role": "scout", "inputs": ["metrics"],
         "produces": "topic_pool", "review": True},
        {"id": "write", "role": "writer", "inputs": ["topic_pool"],
         "produces": "content_pack", "review": True},
        {"id": "publish", "role": "publisher", "inputs": ["content_pack"],
         "produces": "publication", "outbound": True},
        {"id": "measure", "role": "analyst", "inputs": ["publication"], "produces": "metrics"},
    ],
}


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / ".vortocode" / "pipelines"
    d.mkdir(parents=True)
    (d / "content-ops.yaml").write_text(yaml.safe_dump(CONTENT_OPS, allow_unicode=True),
                                        encoding="utf-8")
    return tmp_path


@pytest.fixture
def started(repo):
    store = PipelineStore(str(repo))
    return store, store.start(load_definition(str(repo), "content-ops"))


def _token(store, run):
    return review_fingerprint(store.repo_root, store.load(run.run_id))


def _exec(payload=None, **extra):
    """造一个假执行器，记录它被调用时拿到的输入。"""
    seen = []

    async def run(stage_def, inputs):
        seen.append((stage_def.id, [p.kind for p in inputs]))
        return {"payload": payload or {"ok": stage_def.id}, "summary": f"{stage_def.id} 产出", **extra}

    return run, seen


# ------------------------------------------------------------------ 定义
def test_definition_is_configuration_not_code(repo):
    """加一条流水线 = 加一个 yaml；工序按 kind 声明输入，不绑具体产出物 id。"""
    d = load_definition(str(repo), "content-ops")
    assert [s.id for s in d.stages] == ["scout", "write", "publish", "measure"]
    assert d.stage("write").inputs == ["topic_pool"]
    assert d.stage("publish").outbound is True and d.stage("publish").review is False
    assert load_definition(str(repo), "不存在") is None
    assert load_definition(str(repo), "../../etc/passwd") is None


@pytest.mark.parametrize("bad, msg", [
    ({"stages": [{"id": "a"}]}, "name"),
    ({"name": "x"}, "至少要有一道工序"),
    ({"name": "x", "stages": [{"role": "r"}]}, "缺少 id"),
    ({"name": "x", "stages": [{"id": "a"}, {"id": "a"}]}, "重复"),
])
def test_broken_definition_fails_loudly(bad, msg):
    """坏配置当场报错。**默默跳过一道工序**比报错难查得多。"""
    with pytest.raises(ValueError, match=msg):
        PipelineDef.from_dict(bad)


# ------------------------------------------------------------------ 推进
@pytest.mark.asyncio
async def test_advance_runs_one_stage_and_stops_at_the_gate(started):
    """默认一次只推一道：每道工序都是一次真 LLM 回合，别让一次失误放大到整条线。"""
    store, run = started
    execute, seen = _exec()
    result = await advance(store.repo_root, run.run_id, execute=execute)
    assert result.ran == ["scout"] and result.blocked_on == "scout"
    assert result.status == "awaiting_review"
    assert seen == [("scout", [])]                     # 第一轮没有上轮 metrics，输入为空
    after = store.load(run.run_id)
    assert after.stage("scout").status == "awaiting_review"
    assert after.stage("write").status == "pending"


@pytest.mark.asyncio
async def test_advance_is_idempotent_while_waiting(started):
    """幂等是"同一个动作能挂在 cron/常驻/手点三个入口"的前提。"""
    store, run = started
    execute, seen = _exec()
    await advance(store.repo_root, run.run_id, execute=execute)
    for _ in range(3):
        result = await advance(store.repo_root, run.run_id, execute=execute)
        assert result.ran == [] and result.blocked_on == "scout"
    assert len(seen) == 1                              # 等人批期间一次都没重复烧 token


@pytest.mark.asyncio
async def test_stage_output_becomes_the_next_stage_input(started):
    """交接靠产出物，不靠上下文——这条是整个设计的命题。"""
    store, run = started
    execute, seen = _exec()
    await advance(store.repo_root, run.run_id, execute=execute)
    review(store.repo_root, run.run_id, review_token=_token(store, run), verdict="approve")
    await advance(store.repo_root, run.run_id, execute=execute)
    assert seen[1] == ("write", ["topic_pool"])        # write 拿到了 scout 的产出

    products = ProductStore(store.repo_root)
    pack = products.latest("content_pack")
    pool = products.latest("topic_pool")
    assert pack.inputs == [pool.id]                    # 血缘连上了


@pytest.mark.asyncio
async def test_run_completes_and_then_refuses_further_work(started):
    store, run = started
    execute, _ = _exec()
    for _ in range(6):
        await advance(store.repo_root, run.run_id, execute=execute)
        current = store.load(run.run_id)
        if current.status == "awaiting_review":
            review(store.repo_root, run.run_id, review_token=_token(store, run), verdict="approve")
    done = store.load(run.run_id)
    assert done.status == "done"
    assert [s.status for s in done.stages] == ["done"] * 4
    again = await advance(store.repo_root, run.run_id, execute=execute)
    assert again.reason == "已是终态，无需推进"


@pytest.mark.asyncio
async def test_max_stages_lets_the_caller_drain(started):
    """默认保守，但调用方可以显式要求连推——不给这个口子的话无人值守场景要调 N 次。"""
    store, run = started
    execute, seen = _exec()
    await advance(store.repo_root, run.run_id, execute=execute)
    review(store.repo_root, run.run_id, review_token=_token(store, run), verdict="approve")
    await advance(store.repo_root, run.run_id, execute=execute)
    review(store.repo_root, run.run_id, review_token=_token(store, run), verdict="approve")
    result = await advance(store.repo_root, run.run_id, execute=execute, max_stages=5)
    assert result.ran == ["publish", "measure"] and result.status == "done"


@pytest.mark.asyncio
async def test_metrics_feed_the_next_round(repo):
    """闭环：新一轮的 scout 读得到上一轮的 metrics（同流水线全局最新）。"""
    store = PipelineStore(str(repo))
    ProductStore(str(repo)).create("metrics", pipeline="content-ops",
                                   payload={"views": 900}, summary="上轮数据")
    run = store.start(load_definition(str(repo), "content-ops"))
    execute, seen = _exec()
    await advance(str(repo), run.run_id, execute=execute)
    assert seen[0] == ("scout", ["metrics"])


# ------------------------------------------------------------------ 失败
@pytest.mark.asyncio
async def test_failing_stage_records_the_real_cause(started):
    """失败要带上真死因。裸 str(e) 等于什么都没说——同 dev 流水线的既定教训。"""
    store, run = started

    async def boom(stage_def, inputs):
        raise RuntimeError("relay 502")

    result = await advance(store.repo_root, run.run_id, execute=boom)
    assert result.status == "failed" and result.blocked_on == "scout"
    assert "RuntimeError" in result.reason and "relay 502" in result.reason
    assert store.load(run.run_id).stage("scout").attempts == 1


@pytest.mark.asyncio
async def test_stage_missing_from_definition_stops_instead_of_guessing(started, repo):
    """定义被改过、少了一道工序：如实停下，别猜也别跳过。"""
    store, run = started
    shrunk = dict(CONTENT_OPS, stages=CONTENT_OPS["stages"][1:])
    (repo / ".vortocode" / "pipelines" / "content-ops.yaml").write_text(
        yaml.safe_dump(shrunk, allow_unicode=True), encoding="utf-8")
    execute, _ = _exec()
    result = await advance(store.repo_root, run.run_id, execute=execute)
    assert result.status == "failed" and "已无工序 scout" in result.reason


# ------------------------------------------------------------------ 人批三档
@pytest.mark.asyncio
async def test_defer_holds_without_advancing_or_failing(started):
    """挂起不是失败。cron 下次来照样静静走开，不该刷屏也不该判死。"""
    store, run = started
    execute, seen = _exec()
    await advance(store.repo_root, run.run_id, execute=execute)
    result = review(store.repo_root, run.run_id, review_token=_token(store, run), verdict="defer", comment="这周先不发")
    assert result.status == "awaiting_review" and result.blocked_on == "scout"
    await advance(store.repo_root, run.run_id, execute=execute)
    assert len(seen) == 1                              # 依然没往前走
    assert store.load(run.run_id).stage("scout").note == "这周先不发"


@pytest.mark.asyncio
async def test_reject_records_the_comment_as_a_product_in_the_lineage(started):
    """你的意见进血缘，三个月后还答得出"这版为什么改成这样"。"""
    store, run = started
    execute, _ = _exec()
    await advance(store.repo_root, run.run_id, execute=execute)
    review(store.repo_root, run.run_id, review_token=_token(store, run), verdict="approve")
    await advance(store.repo_root, run.run_id, execute=execute)   # write 产出，等批

    products = ProductStore(store.repo_root)
    v1 = products.latest("content_pack")
    review(store.repo_root, run.run_id, review_token=_token(store, run), verdict="reject", comment="开头太软")

    note = products.latest("review_note")
    assert note.payload["comment"] == "开头太软" and note.inputs == [v1.id]

    after = store.load(run.run_id)
    assert after.stage("write").status == "pending"
    assert note.id in after.stage("write").extra_inputs


@pytest.mark.asyncio
async def test_reject_produces_a_new_version_and_keeps_the_old(started):
    """产出物不可变：重做产出新一版，旧版一条不删。"""
    store, run = started
    execute, seen = _exec()
    await advance(store.repo_root, run.run_id, execute=execute)
    review(store.repo_root, run.run_id, review_token=_token(store, run), verdict="approve")
    await advance(store.repo_root, run.run_id, execute=execute)

    products = ProductStore(store.repo_root)
    v1 = products.latest("content_pack")
    review(store.repo_root, run.run_id, review_token=_token(store, run), verdict="reject", comment="开头太软")
    await advance(store.repo_root, run.run_id, execute=execute)   # 重跑 write

    packs = products.list(kind="content_pack")
    assert len(packs) == 2 and v1.id in {p.id for p in packs}      # 旧版还在
    assert seen[-1][0] == "write" and "review_note" in seen[-1][1]  # 重跑时看得到意见
    assert store.load(run.run_id).stage("write").attempts == 2


@pytest.mark.asyncio
async def test_rollback_target_is_chosen_by_the_caller_not_guessed(started):
    """驳回的可能是选题本身——退回哪一步由你指定，模型不猜。"""
    store, run = started
    execute, _ = _exec()
    await advance(store.repo_root, run.run_id, execute=execute)
    review(store.repo_root, run.run_id, review_token=_token(store, run), verdict="approve")
    await advance(store.repo_root, run.run_id, execute=execute)

    result = review(store.repo_root, run.run_id, review_token=_token(store, run), verdict="reject",
                    comment="选题就不对", rollback_to="scout")
    assert "退回 scout" in result.reason
    after = store.load(run.run_id)
    assert after.stage("scout").status == "pending"     # 退回点及其之后全部重置
    assert after.stage("write").status == "pending"
    assert after.stage("scout").extra_inputs            # 意见挂在退回点上


@pytest.mark.asyncio
async def test_unknown_rollback_target_changes_nothing(started):
    store, run = started
    execute, _ = _exec()
    await advance(store.repo_root, run.run_id, execute=execute)
    result = review(store.repo_root, run.run_id, review_token=_token(store, run), verdict="reject", rollback_to="不存在")
    assert "不是本流水线的工序" in result.reason
    assert store.load(run.run_id).stage("scout").status == "awaiting_review"


def test_review_without_a_pending_gate_is_a_no_op(started):
    store, run = started
    assert "没有等待人批" in review(store.repo_root, run.run_id, review_token=_token(store, run), verdict="approve").reason


def test_unknown_verdict_is_rejected(started):
    store, run = started
    with pytest.raises(ValueError, match="verdict"):
        review(store.repo_root, run.run_id, review_token=_token(store, run), verdict="maybe")


# ------------------------------------------------------------------ 存储硬约束
@pytest.mark.asyncio
async def test_run_id_cannot_escape_the_store(started):
    store, _ = started
    assert store.load("../../etc/passwd") is None
    async def noop(*_a):
        return {}
    missing = await advance(store.repo_root, "../../x", execute=noop)
    assert missing.status == "missing"


def test_runs_dir_is_gitignored_but_definitions_are_not(started, repo):
    """运行状态是运行时产物；流水线定义是用户配置，要能 git add。"""
    ignore = (repo / ".vortocode" / ".gitignore").read_text(encoding="utf-8")
    assert "pipeline_runs/" in ignore
    assert "pipelines/" not in ignore.replace("pipeline_runs/", "")


@pytest.mark.asyncio
async def test_failed_stage_is_retryable_until_the_cap(started):
    """**failed 不是终态**：outbound 被 fail-closed 拒一次（你没带授权来）、relay 抖一下，
    都不该让整轮死掉。但也不能无限重试——一个持续失败的工序在 cron 上每天烧一遍 token，
    那是"永远亮着的红灯"的花钱版本。
    """
    from src.gateway.pipeline import MAX_STAGE_ATTEMPTS
    store, run = started
    calls = {"n": 0}

    async def flaky(stage_def, inputs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("relay 502")
        return {"payload": {"ok": 1}, "summary": "好了"}

    first = await advance(store.repo_root, run.run_id, execute=flaky)
    assert first.status == "failed"
    again = await advance(store.repo_root, run.run_id, execute=flaky)   # 还能再试
    assert again.ran == ["scout"] and again.status == "awaiting_review"
    assert store.load(run.run_id).stage("scout").attempts == 2

    # 反面：一直失败就要停下等人，不能每次 cron 都白烧一轮
    _, dead = started[0], store.start(load_definition(store.repo_root, "content-ops"))

    async def always_boom(stage_def, inputs):
        raise RuntimeError("一直挂")

    for _ in range(MAX_STAGE_ATTEMPTS + 2):
        await advance(store.repo_root, dead.run_id, execute=always_boom)
    final = store.load(dead.run_id)
    assert final.status == "failed"
    assert final.stage("scout").attempts == MAX_STAGE_ATTEMPTS       # 到顶就不再试


@pytest.mark.asyncio
async def test_last_stage_settles_the_run_without_another_call(started):
    """跑完最后一道工序当场转终态，别挂在 running 等下一次 advance——list 上会一直显示"进行中"。"""
    store, run = started
    execute, _ = _exec()
    for stage in ("scout", "write"):
        await advance(store.repo_root, run.run_id, execute=execute)
        review(store.repo_root, run.run_id, review_token=_token(store, run), verdict="approve")
    await advance(store.repo_root, run.run_id, execute=execute)      # publish
    result = await advance(store.repo_root, run.run_id, execute=execute)  # measure = 最后一道
    assert result.ran == ["measure"] and result.status == "done"
