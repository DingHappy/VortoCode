"""手机入口在公网上：光凭账号登录拿不到写入能力。第一台手机首次使用即信任，之后的新钥匙要在 Desktop 上批准；
信任挂在钥匙上，同一台手机重新登录（新的设备记录）不用再批。"""

from datetime import datetime, timezone

import pytest

from src.gateway.mobile_devices import MobileDevices, key_code
from src.scheduling import assistant_watch
from src.scheduling.patrols import PatrolContext
from tests.unit.test_mobile_owner import Key, bind, owner, signed  # noqa: F401 —— 复用写入夹具
from tests.unit.test_mobile_browser import native_handoff, setup  # noqa: F401


def keys():
    return Key().public, Key().public


def device(store):
    return store.issue("手机", 30)["device"]["id"]


def test_first_phone_is_trusted_and_later_phones_wait_for_approval(tmp_path):
    store = MobileDevices(str(tmp_path))
    mine, other = keys(), keys()
    first = store.bind_keys(device(store), *mine)
    assert "bound_at" in first and first["code"] == key_code(*mine)

    stranger = device(store)
    waiting = store.bind_keys(stranger, *other)
    assert waiting["pending"] is True
    assert store.signing_key(stranger, "device") is None                 # 待确认的钥匙签不了任何写入
    assert store.bind_keys(stranger, *other) == waiting                   # 重试拿到同一份申请
    with pytest.raises(ValueError):
        store.bind_keys(stranger, *keys())                                # 一台设备只排一对钥匙
    [request] = store.key_requests()["pending"]
    assert request == {"device_id": stranger, "name": "手机", "requested_at": waiting["requested_at"],
                       "code": key_code(*other)}

    assert store.decide_keys(stranger, True) == {"approved": True, "code": key_code(*other)}
    assert store.signing_key(stranger, "device") == other[0]
    assert {item["code"] for item in store.key_requests()["trusted"]} == {key_code(*mine), key_code(*other)}
    assert store.key_requests()["pending"] == []


def test_same_phone_logging_in_again_needs_no_approval(tmp_path):
    store = MobileDevices(str(tmp_path))
    mine = keys()
    store.bind_keys(device(store), *mine)
    again = device(store)                                                 # 退出后重新登录：新的设备记录
    assert "bound_at" in store.bind_keys(again, *mine)
    assert store.signing_key(again, "approval") == mine[1]


def test_rejected_request_grants_nothing(tmp_path):
    store = MobileDevices(str(tmp_path))
    store.bind_keys(device(store), *keys())
    stranger = device(store)
    store.bind_keys(stranger, *keys())
    assert store.decide_keys(stranger, False)["approved"] is False
    assert store.signing_key(stranger, "device") is None
    assert len(store.key_requests()["trusted"]) == 1
    with pytest.raises(KeyError):
        store.decide_keys(stranger, True)                                 # 已处理的申请不能再批


def test_removing_a_phone_stops_its_signatures_and_first_use_trust_never_reopens(tmp_path):
    store = MobileDevices(str(tmp_path))
    mine = keys()
    phone = device(store)
    store.bind_keys(phone, *mine)
    store.untrust_keys(key_code(*mine))
    assert store.signing_key(phone, "device") is None
    assert store.key_requests()["trusted"] == []
    # 名单被移空也不会回到「首次使用即信任」：否则偷到密码的人只要等你移除手机就能直接绑定
    assert store.bind_keys(device(store), *keys())["pending"] is True
    assert store.bind_keys(device(store), *mine)["pending"] is True       # 被移除的钥匙也要重新批准
    with pytest.raises(KeyError):
        store.untrust_keys("0000-0000")


def test_pending_phone_cannot_write_through_the_gateway(owner, tmp_path):  # noqa: F811
    client, headers, device_id, root = owner
    store = MobileDevices(root)
    store.bind_keys(device(store), *keys())                               # 你的手机早已绑定
    response, device_key, _ = bind(client, headers)
    assert response.status_code == 200 and response.json()["pending"] is True
    assert client.get("/api/mobile/native/bootstrap", headers=headers).json()["capabilities"]["writes"] == []
    denied = signed(client, headers, device_id, "/api/mobile/native/capture",
                    {"request_id": "cap-0009-abcd", "text": "x"}, device_key)
    assert denied.status_code == 401
    store.decide_keys(device_id, True)
    allowed = signed(client, headers, device_id, "/api/mobile/native/capture",
                     {"request_id": "cap-0009-abcd", "text": "x"}, device_key)
    assert allowed.status_code == 200


def test_desktop_routes_list_decide_and_remove(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.web.server import app
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOONE_API_TOKEN", raising=False)
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    store = MobileDevices(str(tmp_path))
    mine = keys()
    store.bind_keys(device(store), *mine)
    stranger = device(store)
    store.bind_keys(stranger, *keys())
    client = TestClient(app)
    listed = client.get("/api/assistant/phone-keys").json()
    assert [item["device_id"] for item in listed["pending"]] == [stranger]
    assert "device" not in str(listed["trusted"])                         # 只给核对码，不外传公钥
    assert client.post(f"/api/assistant/phone-keys/{stranger}", json={"approve": False}).status_code == 200
    assert client.post(f"/api/assistant/phone-keys/{stranger}", json={"approve": True}).status_code == 404
    assert client.post(f"/api/assistant/phone-keys/{stranger}", json={"approve": "yes"}).status_code == 422
    assert client.delete(f"/api/assistant/phone-keys/trusted/{key_code(*mine)}").status_code == 200
    assert client.delete("/api/assistant/phone-keys/trusted/nope").status_code == 404


async def test_patrol_tells_the_owner_once_per_request_and_per_trusted_phone(tmp_path):
    store = MobileDevices(str(tmp_path))
    store.bind_keys(device(store), *keys())
    stranger = device(store)
    store.bind_keys(stranger, *keys())
    sent = []

    async def notify(text):
        sent.append(text)
        return True

    state: dict = {}
    ctx = PatrolContext(root=str(tmp_path), now=datetime.now(timezone.utc), notify=notify, state=state)
    await assistant_watch._patrol_phone_keys(ctx)
    await assistant_watch._patrol_phone_keys(ctx)
    assert len(sent) == 2
    assert "申请" in sent[0] and store.key_requests()["pending"][0]["code"] in sent[0]
    assert "已获得" in sent[1]


async def test_patrol_retries_when_the_notice_was_not_accepted(tmp_path):
    store = MobileDevices(str(tmp_path))
    store.bind_keys(device(store), *keys())
    results = [False, True]

    async def notify(_text):
        return results.pop(0)

    ctx = PatrolContext(root=str(tmp_path), now=datetime.now(timezone.utc), notify=notify, state={})
    await assistant_watch._patrol_phone_keys(ctx)
    await assistant_watch._patrol_phone_keys(ctx)
    assert results == [] and len(ctx.state["phone_keys"]["trusted"]) == 1
