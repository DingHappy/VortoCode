"""手机写操作交给常驻助理执行（ADR-012）：网关沙箱写不了目标 / 撤销记录 / Codex 动作 / 任务单，
所以网关核对完签名就转发；任务单下发要批准钥匙（Face ID）、摘要对上、读过外部内容的不在手机上下发。"""

import pytest
from fastapi.testclient import TestClient

from src.web import owner_ops
from tests.unit.test_mobile_browser import native_handoff, setup  # noqa: F401
from tests.unit.test_mobile_owner import Key, bind, owner, signed  # noqa: F401


def _item(**extra):
    return {"id": "dsp-0123456789", "project": "VortoCode", "title": "修登录", "goal": "登录不再 500",
            "status": "proposed", "tainted": False, "criteria": ["登录 200"], **extra}


@pytest.fixture
def ledger(monkeypatch):
    from src.workflow.tasks import assistant_dispatch
    items = [_item()]
    calls = []

    async def approve(root, dispatch_id, **_kwargs):
        calls.append(("approve", dispatch_id))
        return {**items[0], "status": "sent"}

    def cancel(root, dispatch_id):
        calls.append(("cancel", dispatch_id))
        return {**items[0], "status": "cancelled"}

    monkeypatch.setattr(assistant_dispatch, "list_dispatches", lambda root: list(items))
    monkeypatch.setattr(assistant_dispatch, "approve", approve)
    monkeypatch.setattr(assistant_dispatch, "cancel", cancel)
    return items, calls


async def test_dispatch_rules(tmp_path, ledger):
    items, calls = ledger
    root = str(tmp_path)
    digest = owner_ops.dispatch_view(items[0])["digest"]
    assert (await owner_ops.run(root, "dispatch", {"dispatch_id": "dsp-0123456789", "approve": True, "digest": "x"}))[0] == 409
    status, data = await owner_ops.run(root, "dispatch", {"dispatch_id": "dsp-0123456789", "approve": True, "digest": digest})
    assert status == 200 and data["status_text"] == "已下发" and calls == [("approve", "dsp-0123456789")]
    status, _ = await owner_ops.run(root, "dispatch", {"dispatch_id": "dsp-0123456789", "approve": False})
    assert status == 200 and calls[-1] == ("cancel", "dsp-0123456789")
    items[0] = _item(tainted=True)
    status, data = await owner_ops.run(root, "dispatch", {"dispatch_id": "dsp-0123456789", "approve": True,
                                                          "digest": owner_ops.dispatch_view(items[0])["digest"]})
    assert status == 409 and "Desktop" in data["detail"]                 # 读过外部内容的到电脑上下发
    assert (await owner_ops.run(root, "dispatch", {"dispatch_id": "../x", "approve": True}))[0] == 400
    assert (await owner_ops.run(root, "rm_rf", {}))[0] == 404            # 不认识的操作


def test_writes_go_to_the_assistant_when_the_relay_is_configured(owner, monkeypatch):  # noqa: F811
    client, headers, device_id, root = owner
    _, device, _ = bind(client, headers)
    monkeypatch.setenv("VORTOCODE_APP_RELAY_URL", "http://127.0.0.1:8081")
    monkeypatch.setenv("VORTOCODE_APP_RELAY_TOKEN", "relay-token")
    sent = []

    async def relay(path, body, *, client=None):
        sent.append((path, body))
        return 200, {"goal_id": "g", "objective": "x"}

    import src.gateway.app_relay_client as relay_client
    monkeypatch.setattr(relay_client, "relay", relay)
    response = signed(client, headers, device_id, "/api/mobile/native/capture",
                      {"request_id": "cap-0101-abcd", "text": "周五交周报"}, device)
    assert response.status_code == 200
    assert sent == [("/api/assistant/app-relay/op",
                     {"op": "capture", "args": {"text": "周五交周报", "request_id": "cap-0101-abcd"}})]
    from src.workflow.workbench.personal_goals import PersonalGoals
    assert PersonalGoals(root).list() == []                                # 网关自己没写


def test_dispatch_needs_the_face_id_key_and_a_trusted_phone(owner, ledger):  # noqa: F811
    client, headers, device_id, _root = owner
    assert client.get("/api/mobile/native/dispatches", headers=headers).status_code == 403
    _, device, approval = bind(client, headers)
    [listed] = client.get("/api/mobile/native/dispatches", headers=headers).json()["dispatches"]
    assert listed["title"] == "修登录" and len(listed["digest"]) == 64
    body = {"dispatch_id": listed["id"], "approve": True, "digest": listed["digest"]}
    assert signed(client, headers, device_id, "/api/mobile/native/dispatch", body, device).status_code == 401
    ok = signed(client, headers, device_id, "/api/mobile/native/dispatch", body, approval)
    assert ok.status_code == 200 and ok.json()["status"] == "sent"
    undo = signed(client, headers, device_id, "/api/mobile/native/dispatch",
                  {"dispatch_id": listed["id"], "approve": False}, device)
    assert undo.status_code == 200 and undo.json()["status"] == "cancelled"


def test_relay_op_route_runs_the_operation_inside_the_assistant(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "admin-token")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    monkeypatch.setenv("VORTOCODE_APP_RELAY_TOKEN", "relay-token")
    from src.web.server import app
    local = TestClient(app, client=("127.0.0.1", 50000))
    auth = {"Authorization": "Bearer relay-token"}
    made = local.post("/api/assistant/app-relay/op", headers=auth,
                      json={"op": "capture", "args": {"text": "买牛奶", "request_id": "cap-0202-abcd"}})
    assert made.status_code == 200 and made.json()["objective"] == "买牛奶"
    from src.workflow.workbench.personal_goals import PersonalGoals
    assert [goal["objective"] for goal in PersonalGoals(str(tmp_path)).list()] == ["买牛奶"]
    assert local.post("/api/assistant/app-relay/op", headers=auth, json={"op": "nope", "args": {}}).status_code == 404
    assert TestClient(app).post("/api/assistant/app-relay/op", headers=auth,
                                json={"op": "capture", "args": {}}).status_code == 404   # 非本机来源
