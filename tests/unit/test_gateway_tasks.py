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


def test_ledger_save_leaves_worktree_clean(tmp_path):
    """台账落 .vortocode/tasks/ 后，目标仓库（无自带 .gitignore）git status 仍干净（.vortocode/ 自忽略）。"""
    import subprocess
    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True, text=True)
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    (tmp_path / "f.py").write_text("x=1\n", encoding="utf-8"); git("add", "-A"); git("commit", "-qm", "init")
    TaskLedger(str(tmp_path)).create("dev", "干活")
    assert (tmp_path / ".vortocode" / "tasks").is_dir()
    assert git("status", "--porcelain").stdout.strip() == ""     # 台账对 git status 隐形


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
async def test_runner_join_awaits_completion(tmp_path):
    gate = asyncio.Event()
    done = []

    async def worker(task, on_progress):
        await gate.wait()
        done.append(task.id)
        return "ok"

    runner = TaskRunner(str(tmp_path), worker)
    t = await runner.submit("t")
    gate.set()
    await runner.join(t.id)                              # 等它真跑完（不是提交即返回）
    assert done == [t.id] and runner.get(t.id).status == "done"
    await runner.join("unknown")                        # 未知 id → 立即返回不炸


@pytest.mark.asyncio
async def test_dev_worker_pins_plan_no_cross_attribution(tmp_path, monkeypatch):
    """并发跑两个后台 dev 任务：各自按 task-scoped plan_id 取回**自己**的 branch，绝不串单（#128 评审）。"""
    monkeypatch.chdir(tmp_path)
    import src.agents.main_agent as ma
    import src.web.routers.tasks as tm
    from src.agents import dev_plan as _dp
    from src.gateway.tasks import BackgroundTask

    class _T:
        def __init__(self, name, handler):
            self.name, self.handler = name, handler

    def fake_build(root, on_progress=None, confirm=None, draft_pr=False):
        async def dev_auto(args):
            pid = args["plan_id"]                        # 用调用方钉的 id 落计划，分支由 id 派生
            plan = _dp.DevPlan.new(args["task"], f"vorto/auto-{pid}", "main", plan_id=pid)
            plan.status = "integrated"
            _dp.save_plan(root, plan)
            return f"done {pid}"
        return [_T("dev_auto", dev_auto)]
    monkeypatch.setattr(ma, "build_dev_tools", fake_build)

    a = BackgroundTask.new("dev", "任务A")
    b = BackgroundTask.new("dev", "任务B")
    await asyncio.gather(tm._dev_worker(a, lambda _m: None), tm._dev_worker(b, lambda _m: None))
    assert a.plan_id == f"bg-{a.id}" and a.branch == f"vorto/auto-bg-{a.id}"   # A 拿 A 的
    assert b.plan_id == f"bg-{b.id}" and b.branch == f"vorto/auto-bg-{b.id}"   # B 拿 B 的（不串）


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


def test_rest_open_pr_requires_task_done(client_with_fake_runner):
    """只对**成功完成**的任务开 PR——running/failed 都拒（即使已有 vorto/ 分支，#128 评审）。"""
    client, runner = client_with_fake_runner
    t = runner.ledger.create("dev", "x")
    t.status = "running"; t.branch = "vorto/auto-x"
    runner.ledger.save(t)
    r = client.post(f"/api/tasks/{t.id}/open_pr")
    assert r.status_code == 400 and "未成功完成" in r.json()["detail"]
    # 标 done 但有 error（失败）→ 也拒
    t2 = runner.ledger.create("dev", "y")
    t2.status = "done"; t2.error = "boom"; t2.branch = "vorto/auto-y"
    runner.ledger.save(t2)
    assert client.post(f"/api/tasks/{t2.id}/open_pr").status_code == 400


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
