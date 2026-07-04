"""IM 桥内嵌 serve（b3 PR-5）单测——单进程唯一状态所有者 + 通知三路收口。

假 adapter（不触网）驱动：内嵌登记/注销、共享 runner 的 kind 分发、通知三路
（台账/WS/IM）都到、凭证 fail-closed。真机端到端等用户 IM 凭证（与 D2 一起验）。
"""

import asyncio

import pytest

pytest.importorskip("fastapi")

from src.gateway import im_service  # noqa: E402


class FakeAdapter:
    """最小通道 adapter：记录出站消息；poll 挂起（测试不走轮询）。"""
    edits_supported = True

    def __init__(self):
        self.sent: list = []

    async def send_text(self, text):
        self.sent.append(str(text))

    async def send_confirm(self, text, cid):
        self.sent.append(f"[confirm:{cid}] {text}")

    async def poll(self):
        while True:
            await asyncio.sleep(3600)
            yield None                                       # pragma: no cover

    async def ack_callback(self, ev):
        pass

    async def close(self):
        pass


class FakeRunner:
    """最小共享 runner：记录 submit 与订阅。"""
    def __init__(self):
        self.submitted: list = []
        self.subs: list = []

    async def submit(self, prompt, kind="dev"):
        from src.gateway.tasks import BackgroundTask
        t = BackgroundTask.new(kind, prompt)
        self.submitted.append((kind, prompt))
        return t

    def subscribe(self, cb):
        self.subs.append(cb)
        return lambda: None


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    im_service.stop_embedded()                               # 每例后注销，防串测


# ------------------------------------------------------------ 内嵌装配：共享 runner + kind 分发
@pytest.mark.asyncio
async def test_start_embedded_shares_runner_and_registers_dispatch(tmp_path, monkeypatch):
    from src.web.routers import tasks as tr
    adapter, runner = FakeAdapter(), FakeRunner()
    bridge, _ = im_service.start_embedded("telegram", str(tmp_path),
                                          adapter=adapter, owner="42", runner=runner)
    assert im_service.current_bridge() is bridge
    assert bridge._on_task_update in runner.subs             # IM 收任务进度（与 WS 同一订阅集）
    assert tr._IM_WORKER is not None                         # kind 分发已登记
    # IM 的 /task 提交走共享 runner、kind="im-dev"
    await bridge._submit_task("修个 bug")
    assert runner.submitted == [("im-dev", "修个 bug")]
    # 注销后干净
    im_service.stop_embedded()
    assert im_service.current_bridge() is None and tr._IM_WORKER is None


@pytest.mark.asyncio
async def test_dev_worker_dispatches_im_kind_to_bridge_worker(tmp_path, monkeypatch):
    """共享 runner 的 worker：kind="im-dev" 路由给 IM worker（保住在跑中的按钮确认 UX）。"""
    from src.gateway.tasks import BackgroundTask
    from src.web.routers import tasks as tr

    async def _im_worker(task, on_progress):
        return f"im 跑的: {task.prompt}"

    tr.register_im_worker(_im_worker)
    try:
        t = BackgroundTask.new("im-dev", "任务A")
        out = await tr._dev_worker(t, lambda _m: None)
        assert out == "im 跑的: 任务A"                        # 分发命中，没走默认 dev_auto 路径
    finally:
        tr.register_im_worker(None)


@pytest.mark.asyncio
async def test_standalone_bridge_keeps_own_runner(tmp_path):
    """standalone（vc im）：不注入 runner → 懒建自己的、/task kind 仍是 dev（向后兼容）。"""
    from src.im.bridge import IMBridge
    bridge = IMBridge(str(tmp_path), FakeAdapter(), "42", channel="telegram")
    assert bridge._shared_runner is False
    assert bridge._runner is None                            # 懒建（用到才有）


# ------------------------------------------------------------ 通知三路（#129 收口）
@pytest.mark.asyncio
async def test_notifier_delivers_three_ways(tmp_path, monkeypatch):
    """make_notifier：台账 + WS 广播 + IM 推 owner 三路都到；一路挂不拖另两路。"""
    import src.web.routers.realtime as rt
    from src.web.routers import tasks as tr

    ws_got: list = []
    monkeypatch.setattr(rt, "broadcast_notice", ws_got.append)
    adapter = FakeAdapter()
    im_service.start_embedded("telegram", str(tmp_path),
                              adapter=adapter, owner="42", runner=FakeRunner())
    notify = tr.make_notifier(str(tmp_path))
    await notify("cron 作业 nightly 跑完：全绿")
    assert any("nightly" in n.get("text", "") for n in tr.load_notices(str(tmp_path), 10))  # ① 台账
    assert ws_got and "nightly" in ws_got[0]                 # ② WS 广播
    assert adapter.sent and "nightly" in adapter.sent[-1]    # ③ IM 推 owner


@pytest.mark.asyncio
async def test_notifier_without_bridge_still_two_ways(tmp_path, monkeypatch):
    """没内嵌 bridge：IM 路 no-op，台账/WS 照常（不抛、不丢）。"""
    import src.web.routers.realtime as rt
    from src.web.routers import tasks as tr
    ws_got: list = []
    monkeypatch.setattr(rt, "broadcast_notice", ws_got.append)
    notify = tr.make_notifier(str(tmp_path))
    await notify("值班发现：CI 红了")
    assert any("CI 红" in n.get("text", "") for n in tr.load_notices(str(tmp_path), 10))
    assert ws_got


@pytest.mark.asyncio
async def test_notify_owner_im_failure_swallowed(tmp_path):
    """IM 发送炸了：不抛、不拖垮调度循环（bridge._safe_send 本身 best-effort，双层兜底）。"""
    class BoomAdapter(FakeAdapter):
        async def send_text(self, text):
            raise RuntimeError("网断了")

    im_service.start_embedded("telegram", str(tmp_path),
                              adapter=BoomAdapter(), owner="42", runner=FakeRunner())
    assert isinstance(await im_service.notify_owner("hi"), bool)   # 不抛即可（消息丢但台账/WS 已有）
    assert await im_service.notify_owner("hi2") is not None


# ------------------------------------------------------------ 凭证 fail-closed
def test_build_adapter_fail_closed_on_missing_creds(monkeypatch):
    for k in ("VORTOCODE_TG_TOKEN", "VORTOCODE_TG_OWNER_ID",
              "VORTOCODE_DD_CLIENT_ID", "VORTOCODE_DD_CLIENT_SECRET", "VORTOCODE_DD_OWNER_ID"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(im_service.IMConfigError, match="VORTOCODE_TG_TOKEN"):
        im_service.build_adapter("telegram")
    with pytest.raises(im_service.IMConfigError, match="VORTOCODE_DD_CLIENT_ID"):
        im_service.build_adapter("dingtalk")
    with pytest.raises(im_service.IMConfigError, match="未知"):
        im_service.build_adapter("wechat")


def test_server_start_fail_closed_on_im_without_creds(monkeypatch):
    """vc server --im telegram 但没配凭证：启动前就拒（显式要了就不能静默没有）。"""
    for k in ("VORTOCODE_TG_TOKEN", "VORTOCODE_TG_OWNER_ID"):
        monkeypatch.delenv(k, raising=False)
    from src.web.server import start_server
    with pytest.raises(SystemExit, match="VORTOCODE_TG_TOKEN"):
        start_server(im="telegram")
