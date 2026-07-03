"""D1 v1 · 后台任务运行时（src/gateway/tasks.py）+ REST 路由 + vcs draft 参数。

不真跑流水线：注入假 worker，聚焦①台账 write-ahead + 崩溃恢复 ②并发上限排队 ③取消 ④异常→failed
⑤worker 设 plan_id/branch ⑥REST 端点鉴权/校验/404/open_pr 硬闸 ⑦gh --draft 传导。
"""
import asyncio

import pytest

from src.gateway import TaskRunner, bg_concurrency
from src.gateway.tasks import BackgroundTask, TaskLedger


# --------------------------------------------------------------------- 台账
def test_ledger_write_ahead_and_recover(tmp_path):
    led = TaskLedger(str(tmp_path))
    t = led.create("dev", "做个事")
    assert t.status == "queued"
    assert led.load(t.id).prompt == "做个事"           # 提交即落盘（write-ahead）

    # 模拟"崩在半路"：手工把状态改 running 落盘，然后 recover
    t.status = "running"
    led.save(t)
    recovered = led.recover_interrupted()
    assert [r.id for r in recovered] == [t.id]
    assert led.load(t.id).status == "interrupted"      # running → interrupted（可 dev_resume 续跑）


def test_ledger_roundtrip_and_list_sorted(tmp_path):
    led = TaskLedger(str(tmp_path))
    a = led.create("dev", "A")
    b = led.create("dev", "B")
    ids = {t.id for t in led.list()}
    assert ids == {a.id, b.id}
    assert led.load("nope") is None
    assert BackgroundTask.from_dict({"id": "x", "kind": "dev", "prompt": "p", "extra": 1}).id == "x"


def test_bg_concurrency_env(monkeypatch):
    monkeypatch.delenv("VORTOCODE_BG_TASKS", raising=False)
    assert bg_concurrency() == 2
    monkeypatch.setenv("VORTOCODE_BG_TASKS", "5")
    assert bg_concurrency() == 5
    monkeypatch.setenv("VORTOCODE_BG_TASKS", "junk")
    assert bg_concurrency() == 2


# --------------------------------------------------------------------- 运行时
@pytest.mark.asyncio
async def test_runner_submit_runs_to_done(tmp_path):
    async def worker(task, on_progress):
        on_progress("干活中")
        task.branch = "vorto/auto-xyz"
        task.plan_id = "plan-1"
        return "干完了"

    runner = TaskRunner(str(tmp_path), worker)
    t = await runner.submit("做个事")
    await runner._running[t.id]                          # 等后台任务跑完
    done = runner.get(t.id)
    assert done.status == "done" and done.result == "干完了"
    assert done.branch == "vorto/auto-xyz" and done.plan_id == "plan-1"
    assert "干活中" in done.log


@pytest.mark.asyncio
async def test_runner_respects_concurrency_cap(tmp_path):
    gate = asyncio.Event()
    peak = {"cur": 0, "max": 0}

    async def worker(task, on_progress):
        peak["cur"] += 1
        peak["max"] = max(peak["max"], peak["cur"])
        await gate.wait()                                # 卡住直到放行 → 观察并发峰值
        peak["cur"] -= 1
        return "ok"

    runner = TaskRunner(str(tmp_path), worker, max_concurrent=1)
    ts = [await runner.submit(f"t{i}") for i in range(3)]
    await asyncio.sleep(0.05)
    assert peak["max"] == 1                              # 上限 1：任意时刻最多 1 个在跑
    gate.set()
    await asyncio.gather(*[runner._running[t.id] for t in ts if t.id in runner._running])
    await asyncio.sleep(0.02)
    assert all(runner.get(t.id).status == "done" for t in ts)


@pytest.mark.asyncio
async def test_runner_cancel(tmp_path):
    async def worker(task, on_progress):
        await asyncio.sleep(10)
        return "不该到这"

    runner = TaskRunner(str(tmp_path), worker)
    t = await runner.submit("长任务")
    await asyncio.sleep(0.02)
    assert runner.cancel(t.id) is True
    with pytest.raises(asyncio.CancelledError):
        await runner._running.get(t.id, _done_future())
    await asyncio.sleep(0.02)
    assert runner.get(t.id).status == "cancelled"


@pytest.mark.asyncio
async def test_runner_worker_exception_marks_failed(tmp_path):
    async def worker(task, on_progress):
        raise RuntimeError("炸了")

    runner = TaskRunner(str(tmp_path), worker)
    t = await runner.submit("会炸的任务")
    await runner._running[t.id]
    got = runner.get(t.id)
    assert got.status == "failed" and "炸了" in got.error


def _done_future():
    f = asyncio.get_event_loop().create_future()
    f.set_result(None)
    return f


# --------------------------------------------------------------------- REST 路由
@pytest.fixture
def client_with_fake_runner(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient
    import src.web.routers.tasks as tasks_mod
    from src.web.server import app

    async def worker(task, on_progress):
        task.branch = "vorto/auto-rest"
        return "done"

    runner = TaskRunner(str(tmp_path), worker)
    monkeypatch.setattr(tasks_mod, "_RUNNER", runner)
    return TestClient(app), runner


def test_rest_submit_validation_and_list(client_with_fake_runner):
    client, runner = client_with_fake_runner
    assert client.post("/api/tasks", json={}).status_code == 400   # 缺 prompt
    r = client.post("/api/tasks", json={"prompt": "做个事"})
    assert r.status_code == 200 and r.json()["id"]
    tid = r.json()["id"]
    lst = client.get("/api/tasks").json()["tasks"]
    assert any(t["id"] == tid for t in lst)
    assert client.get(f"/api/tasks/{tid}").json()["id"] == tid
    assert client.get("/api/tasks/nonexistent").status_code == 404


def test_rest_open_pr_guards_non_vorto_branch(client_with_fake_runner, monkeypatch):
    client, runner = client_with_fake_runner
    # 造一个产出非 vorto 分支的任务 → open_pr 必须硬拒
    t = runner.ledger.create("dev", "x")
    t.status = "done"
    t.branch = "main"
    runner.ledger.save(t)
    r = client.post(f"/api/tasks/{t.id}/open_pr")
    assert r.status_code == 400 and "vorto/" in r.json()["detail"]
    # 无分支
    t2 = runner.ledger.create("dev", "y")
    t2.status = "done"
    runner.ledger.save(t2)
    assert client.post(f"/api/tasks/{t2.id}/open_pr").status_code == 400
    assert client.post("/api/tasks/nope/open_pr").status_code == 404


def test_rest_open_pr_draft_for_vorto_branch(client_with_fake_runner, monkeypatch):
    client, runner = client_with_fake_runner
    import src.agents.vcs as vcs
    captured = {}

    def fake_push_open(repo, branch, title, body="", base="main", remote="origin", draft=False):
        captured.update(branch=branch, draft=draft)
        return {"ok": True, "pushed": True, "url": "http://pr/1", "error": ""}
    monkeypatch.setattr(vcs, "push_and_open_pr", fake_push_open)

    t = runner.ledger.create("dev", "z")
    t.status = "done"
    t.branch = "vorto/auto-abc"
    runner.ledger.save(t)
    r = client.post(f"/api/tasks/{t.id}/open_pr")
    assert r.status_code == 200 and r.json()["url"] == "http://pr/1"
    assert captured["draft"] is True and captured["branch"] == "vorto/auto-abc"   # 后台产出开 draft PR


# --------------------------------------------------------------------- vcs draft 传导
def test_vcs_open_pr_passes_draft_flag(monkeypatch):
    import src.agents.vcs as vcs
    monkeypatch.setattr(vcs.shutil, "which", lambda _n: "/usr/bin/gh")
    seen = {}

    def fake_run(cmd, cwd=None, capture_output=True, text=True):
        seen["cmd"] = cmd

        class R:
            returncode = 0
            stdout = "https://github.com/x/y/pull/9"
            stderr = ""
        return R()
    monkeypatch.setattr(vcs.subprocess, "run", fake_run)

    vcs.open_pr("/repo", "vorto/x", "标题", "正文", "main", draft=True)
    assert "--draft" in seen["cmd"]
    vcs.open_pr("/repo", "vorto/x", "标题", "正文", "main", draft=False)
    assert "--draft" not in seen["cmd"]
