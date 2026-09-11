"""审批台的 REST 面：读要全、写要窄。

IM 负责"叫人"，这条路负责"审阅"——一屏几十行的选题池在手机上读不了。两条路**共用同一份
状态**（`.vortocode/pipeline_runs/` + 产出物台账），不是两套；所以这里测的是
"web 上批完，CLI 看到的是同一件事"。
"""
import pytest
import yaml
from fastapi.testclient import TestClient

from src.gateway.pipeline import PipelineStore, load_definition
from src.gateway.products import ProductStore

DEF = {"name": "content-ops", "stages": [
    {"id": "scout", "produces": "topic_pool", "review": True},
    {"id": "write", "inputs": ["topic_pool"], "produces": "content_pack"}]}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    d = tmp_path / ".vortocode" / "pipelines"
    d.mkdir(parents=True)
    (d / "content-ops.yaml").write_text(yaml.safe_dump(DEF, allow_unicode=True), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def client(repo):
    from src.web.server import app
    return TestClient(app)


def _waiting(repo, *, tainted=False, payload=None):
    store = PipelineStore(str(repo))
    run = store.start(load_definition(str(repo), "content-ops"))
    product = ProductStore(str(repo)).create(
        "topic_pool", payload=payload or {"topics": [{"topic": "MCP 生态"}]},
        summary="3 个候选", pipeline=run.pipeline, run_id=run.run_id, stage="scout",
        tainted=tainted, taint_reason="来自 web_search" if tainted else "")
    stage = run.stage("scout")
    stage.status, stage.product_id, stage.tokens = "awaiting_review", product.id, 1200
    run.status = "awaiting_review"
    store.save(run)
    return run


# ------------------------------------------------------------------ 读
def test_the_list_shows_live_runs_with_the_waiting_ones_first(client, repo):
    """第一屏该是"该你管的"，不是历史归档。"""
    _waiting(repo)
    PipelineStore(str(repo)).start(load_definition(str(repo), "content-ops"))
    body = client.get("/api/pipelines").json()
    assert body["awaiting"] == 1
    assert body["runs"][0]["status"] == "awaiting_review"


def test_finished_runs_are_hidden_unless_asked_for(client, repo):
    run = _waiting(repo)
    store = PipelineStore(str(repo))
    done = store.load(run.run_id)
    done.status = "done"
    store.save(done)
    assert client.get("/api/pipelines").json()["runs"] == []
    assert client.get("/api/pipelines?include_done=true").json()["runs"]


def test_detail_carries_the_full_payload_and_the_stage_spec(client, repo):
    """**批之前要能看见完整的东西**——列表给摘要，详情给全量。"""
    run = _waiting(repo, payload={"topics": [{"topic": "MCP 生态", "why_now": "社区井喷"}]})
    body = client.get(f"/api/pipelines/{run.run_id}").json()
    scout = body["stages"][0]
    assert scout["product"]["payload"]["topics"][0]["why_now"] == "社区井喷"
    assert scout["spec"]["review"] is True          # 这道要人批，界面据此显示回执区


def test_external_provenance_is_visible(client, repo):
    run = _waiting(repo, tainted=True)
    body = client.get(f"/api/pipelines/{run.run_id}").json()
    assert body["stages"][0]["product"]["tainted"] is True
    assert "web_search" in body["stages"][0]["product"]["taint_reason"]


def test_an_unreadable_product_is_reported_not_silently_empty(client, repo):
    """**"还没跑"和"台账坏了"在审批时含义完全不同**，不能长成一个样子。"""
    run = _waiting(repo)
    store = PipelineStore(str(repo))
    live = store.load(run.run_id)
    live.stage("scout").product_id = "prod-topic_pool-不存在"
    store.save(live)
    body = client.get(f"/api/pipelines/{run.run_id}").json()
    assert body["stages"][0]["product"]["missing"] is True


def test_missing_run_is_404(client, repo):
    assert client.get("/api/pipelines/prun-没有这个").status_code == 404


def test_lineage_answers_where_this_came_from(client, repo):
    """审批时最该看的就是"这东西从哪来"——尤其带污点的那些。"""
    run = _waiting(repo, tainted=True)
    pid = PipelineStore(str(repo)).load(run.run_id).stage("scout").product_id
    body = client.get(f"/api/pipelines/{run.run_id}/lineage/{pid}").json()
    assert body["lineage"][0]["id"] == pid and body["lineage"][0]["tainted"] is True


# ------------------------------------------------------------------ 写
def test_approve_moves_the_run_forward(client, repo):
    run = _waiting(repo)
    r = client.post(f"/api/pipelines/{run.run_id}/review", json={"verdict": "approve"})
    assert r.status_code == 200
    assert PipelineStore(str(repo)).load(run.run_id).stage("scout").status == "done"


def test_reject_without_a_reason_is_refused_with_the_reason_why(client, repo):
    """驳回不给理由，重跑出来还是原样——而且界面要能把**服务端说的原因**原样显示出来。"""
    run = _waiting(repo)
    r = client.post(f"/api/pipelines/{run.run_id}/review",
                    json={"verdict": "reject", "comment": "   "})
    assert r.status_code == 400 and "为什么" in r.json()["detail"]
    assert PipelineStore(str(repo)).load(run.run_id).status == "awaiting_review"


def test_reject_records_the_comment_into_lineage(client, repo):
    """意见落成 review_note 进血缘——三个月后还答得出"这版为什么改成这样"。"""
    run = _waiting(repo)
    client.post(f"/api/pipelines/{run.run_id}/review",
                json={"verdict": "reject", "comment": "角度太窄"})
    note = ProductStore(str(repo)).latest("review_note")
    assert note.payload["comment"] == "角度太窄" and note.payload["reviewer"] == "web"


def test_an_unknown_verdict_is_refused(client, repo):
    run = _waiting(repo)
    assert client.post(f"/api/pipelines/{run.run_id}/review",
                       json={"verdict": "删库"}).status_code == 400


def test_there_is_no_endpoint_to_edit_a_product(client, repo):
    """**产出物不给人改。** 人改了它血缘就断了——要改就驳回重做，意见进血缘。
    这条断的是"这个口子没有被顺手加出来"。
    """
    run = _waiting(repo)
    pid = PipelineStore(str(repo)).load(run.run_id).stage("scout").product_id
    for method, path in [("put", f"/api/pipelines/{run.run_id}/products/{pid}"),
                         ("post", f"/api/pipelines/{run.run_id}/products/{pid}"),
                         ("delete", f"/api/pipelines/{run.run_id}/products/{pid}")]:
        assert getattr(client, method)(path).status_code in (404, 405)


# ------------------------------------------------------------------ 两条路同一份状态
def test_the_web_verdict_is_what_the_cli_sees(client, repo):
    """web 上批完，CLI 看到的必须是同一件事——两条路共用同一份状态，不是两套。"""
    run = _waiting(repo)
    client.post(f"/api/pipelines/{run.run_id}/review",
                json={"verdict": "defer", "comment": "这周先不发"})
    reloaded = PipelineStore(str(repo)).load(run.run_id)
    assert reloaded.status == "awaiting_review" and reloaded.stage("scout").note == "这周先不发"
