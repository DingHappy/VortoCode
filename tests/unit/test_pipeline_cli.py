"""`vc pipeline` 入口：退出码语义 + 对外动作的 fail-closed 默认。

退出码是给 cron/脚本看的，所以它本身就是行为契约的一部分——"半夜推不动"和"半夜炸了"
必须能被区分，否则台账里全是绿的，人永远不知道该去看哪一条。
"""
import pytest
import yaml

from src.gateway.pipeline import PipelineStore, review_fingerprint
from src.gateway.pipeline_cli import run_pipeline_cli
from src.gateway.products import ProductStore

DEF = {
    "name": "content-ops",
    "stages": [
        {"id": "scout", "produces": "topic_pool", "review": True},
        {"id": "publish", "role": "courier", "inputs": ["topic_pool"],
         "produces": "publication", "outbound": True},
    ],
}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    d = tmp_path / ".vortocode" / "pipelines"
    d.mkdir(parents=True)
    (d / "content-ops.yaml").write_text(yaml.safe_dump(DEF, allow_unicode=True), encoding="utf-8")
    # 对外工序要真有出口才谈得上"要不要放行"（第一道闸见 test_outbound_is_real.py）。
    agents = tmp_path / ".vortocode" / "agents"
    agents.mkdir(parents=True)
    (agents / "courier.md").write_text(
        "---\nname: courier\ndescription: 交付\ntools: deliver\n---\n你是交付者。\n",
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _reply(monkeypatch, text='{"topics": [], "summary": "空"}'):
    from src.agents import agent_loop

    async def run_turn(self, prompt, mode="plan"):
        return text

    monkeypatch.setattr(agent_loop.MainAgent, "run_turn", run_turn)


# ------------------------------------------------------------------ 退出码语义
@pytest.mark.asyncio
async def test_empty_list_is_quiet_success(repo):
    assert await run_pipeline_cli("list") == 0


@pytest.mark.asyncio
async def test_awaiting_review_exits_nonzero_so_cron_surfaces_it(repo, monkeypatch, capsys):
    """等人批必须非零退出。静静绿着 = cron 台账里没有任何迹象说"该你看了"。"""
    _reply(monkeypatch)
    assert await run_pipeline_cli("start", "content-ops") == 0
    run_id = PipelineStore(str(repo)).list()[0].run_id
    assert await run_pipeline_cli("advance", run_id) == 1        # 推完停在闸门 → 非零
    assert await run_pipeline_cli("list") == 1                   # 列表也报"有事等你"
    assert "awaiting_review" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_failure_also_exits_nonzero(repo, monkeypatch):
    from src.agents import agent_loop

    async def boom(self, prompt, mode="plan"):
        raise RuntimeError("relay 502")

    monkeypatch.setattr(agent_loop.MainAgent, "run_turn", boom)
    await run_pipeline_cli("start", "content-ops")
    run_id = PipelineStore(str(repo)).list()[0].run_id
    assert await run_pipeline_cli("advance", run_id) == 1


@pytest.mark.asyncio
async def test_unknown_names_are_reported_not_silently_ignored(repo):
    assert await run_pipeline_cli("start", "没这条流水线") == 1
    assert await run_pipeline_cli("show", "prun-nope") == 1
    assert await run_pipeline_cli("advance", "prun-nope") == 1


# ------------------------------------------------------------------ 对外动作默认拒绝
@pytest.mark.asyncio
async def test_outbound_needs_an_explicit_yes(repo, monkeypatch, capsys):
    """不给 --yes 就没有确认通道 → outbound 工序 fail-closed。

    终端里静默把东西发出去，是这套设计最不该发生的事。
    """
    _reply(monkeypatch)
    await run_pipeline_cli("start", "content-ops")
    run_id = PipelineStore(str(repo)).list()[0].run_id
    await run_pipeline_cli("advance", run_id)
    await run_pipeline_cli("review", run_id, verdict="approve", review_token=review_fingerprint(str(repo), PipelineStore(str(repo)).load(run_id)))

    assert await run_pipeline_cli("advance", run_id) == 1
    assert "fail-closed" in capsys.readouterr().out

    assert await run_pipeline_cli("advance", run_id, yes=True) == 0
    assert PipelineStore(str(repo)).load(run_id).status == "done"


# ------------------------------------------------------------------ 人批三档
async def test_cli_requires_the_version_shown_before_approval(repo, monkeypatch, capsys):
    _reply(monkeypatch)
    await run_pipeline_cli("start", "content-ops")
    store = PipelineStore(str(repo))
    run_id = store.list()[0].run_id
    await run_pipeline_cli("advance", run_id)
    assert await run_pipeline_cli("review", run_id, verdict="approve") == 1
    await run_pipeline_cli("show", run_id)
    expected = review_fingerprint(str(repo), store.load(run_id))
    assert f"--review-token {expected}" in capsys.readouterr().out
    current = store.load(run_id)
    current.stages[0].attempts += 1
    assert store.save(current)
    assert await run_pipeline_cli("review", run_id, verdict="approve", review_token=expected) == 1
    assert store.load(run_id).status == "awaiting_review"


@pytest.mark.asyncio
async def test_defer_keeps_it_waiting(repo, monkeypatch):
    _reply(monkeypatch)
    await run_pipeline_cli("start", "content-ops")
    run_id = PipelineStore(str(repo)).list()[0].run_id
    await run_pipeline_cli("advance", run_id)
    assert await run_pipeline_cli("review", run_id, verdict="defer", comment="这周先不发", review_token=review_fingerprint(str(repo), PipelineStore(str(repo)).load(run_id))) == 1
    assert PipelineStore(str(repo)).load(run_id).status == "awaiting_review"


@pytest.mark.asyncio
async def test_reject_records_the_comment_and_reruns(repo, monkeypatch):
    _reply(monkeypatch)
    await run_pipeline_cli("start", "content-ops")
    run_id = PipelineStore(str(repo)).list()[0].run_id
    await run_pipeline_cli("advance", run_id)
    assert await run_pipeline_cli("review", run_id, verdict="reject", comment="角度太窄", review_token=review_fingerprint(str(repo), PipelineStore(str(repo)).load(run_id))) == 0

    note = ProductStore(str(repo)).latest("review_note")
    assert note.payload["comment"] == "角度太窄" and note.payload["reviewer"] == "cli"
    assert PipelineStore(str(repo)).load(run_id).stage("scout").status == "pending"


@pytest.mark.asyncio
async def test_review_needs_a_verdict(repo):
    await run_pipeline_cli("start", "content-ops")
    run_id = PipelineStore(str(repo)).list()[0].run_id
    assert await run_pipeline_cli("review", run_id) == 1


# ------------------------------------------------------------------ show
@pytest.mark.asyncio
async def test_show_reports_products_tokens_and_taint(repo, monkeypatch, capsys):
    """show 是人看运营情况的地方——确定性投影，不是让主 agent 转述一遍。"""
    from src.agents import agent_loop
    from src.llm.client import add_usage

    async def run_turn(self, prompt, mode="plan"):
        add_usage(700, 77, model="test")
        return '{"topics": [1], "summary": "1 个候选"}'

    monkeypatch.setattr(agent_loop.MainAgent, "run_turn", run_turn)
    await run_pipeline_cli("start", "content-ops")
    run_id = PipelineStore(str(repo)).list()[0].run_id
    await run_pipeline_cli("advance", run_id)
    capsys.readouterr()

    await run_pipeline_cli("show", run_id)
    out = capsys.readouterr().out
    assert "scout" in out and "777 tok" in out and "1 个候选" in out


@pytest.mark.asyncio
async def test_show_marks_external_provenance(repo, monkeypatch, capsys):
    _reply(monkeypatch)
    await run_pipeline_cli("start", "content-ops")
    run_id = PipelineStore(str(repo)).list()[0].run_id
    await run_pipeline_cli("advance", run_id)

    store = PipelineStore(str(repo))
    run = store.load(run_id)
    products = ProductStore(str(repo))
    product = products.load(run.stage("scout").product_id)
    product.tainted = True
    product.taint_reason = "来自 web_search"
    products.save(product)

    capsys.readouterr()
    await run_pipeline_cli("show", run_id)
    assert "⚠外部来源" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_unknown_action_is_a_usage_error(repo):
    assert await run_pipeline_cli("绝不存在的动作") == 2


# ------------------------------------------------------------------ 定时开轮防堆积
@pytest.mark.asyncio
async def test_if_idle_does_not_pile_up_on_a_run_that_is_still_waiting(repo, capsys):
    """**昨天那轮还等你批，今天又开一轮**——两轮抢同一个人的注意力，而 signals 去重会让
    第二轮拿到的素材更差。定时开轮必须挡住这个。
    """
    assert await run_pipeline_cli("start", "content-ops") == 0
    before = len(PipelineStore(str(repo)).list())

    assert await run_pipeline_cli("start", "content-ops", if_idle=True) == 0   # 静静走开
    assert len(PipelineStore(str(repo)).list()) == before
    assert "本次不开新的" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_if_idle_starts_when_nothing_is_live(repo):
    assert await run_pipeline_cli("start", "content-ops", if_idle=True) == 0
    assert len(PipelineStore(str(repo)).list()) == 1


@pytest.mark.asyncio
async def test_a_finished_round_does_not_block_the_next_one(repo):
    """终态的运行不算"在跑"——否则跑完一轮之后就再也开不出新的了。"""
    await run_pipeline_cli("start", "content-ops", if_idle=True)
    store = PipelineStore(str(repo))
    run = store.list()[0]
    run.status = "done"
    store.save(run)

    assert await run_pipeline_cli("start", "content-ops", if_idle=True) == 0
    assert len(store.list()) == 2


@pytest.mark.asyncio
async def test_without_the_flag_start_still_always_starts(repo):
    """人手动敲 start 就是要开一轮——别让防堆积把显式意图也挡了。"""
    await run_pipeline_cli("start", "content-ops")
    await run_pipeline_cli("start", "content-ops")
    assert len(PipelineStore(str(repo)).list()) == 2
