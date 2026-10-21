"""派活和真实项目运行时的接口约定：助理端的刷新 / 读问题 / 回答直接打到项目运行时的真实路由上。

单元测试里的假运行时是照着我们自己的理解写的，对不上的地方测不出来（2026-10-20 核对时差点因此误判）。
这里只把模型执行换成假的，任务台账、提问状态机、HTTP 路由和视图都是真的。全部离线。
"""
from __future__ import annotations

import json

import httpx
import pytest

from src.agents.dev_plan import Block, DevPlan, load_plan, save_checkpoint
from src.workflow.tasks import assistant_dispatch as dispatch
from src.workflow.tasks.dev_questions import DevelopmentQuestions
from src.workflow.tasks.task_recovery import plan_revision
from src.workflow.tasks.tasks import TaskRunner

TOKEN = "contract-test-token"
SUFFIX = "0123456789"


@pytest.fixture
def world(tmp_path, monkeypatch):
    from src.web.routers import tasks as task_routes
    project = tmp_path / "project"
    project.mkdir()
    assistant = tmp_path / "assistant"
    assistant.mkdir()
    registry = tmp_path / "resident_projects.json"
    registry.write_text(json.dumps({"projects": [{"name": "VortoCode", "repo_root": str(project), "port": 8082}]}))
    monkeypatch.setenv("VORTOCODE_RESIDENT_PROJECTS", str(registry))
    monkeypatch.setenv("VORTOCODE_API_TOKEN", TOKEN)
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    monkeypatch.chdir(project)                         # 项目运行时的路由以工作目录为仓库根

    async def execute(task, progress):
        return "改好了：TIMEOUT=30，补了单测"
    runner = TaskRunner(str(project), execute, max_concurrent=1)
    monkeypatch.setattr(task_routes, "_RUNNER", runner)
    plan = DevPlan.new("改登录超时", "vorto/login-timeout", "main", plan_id="plan-login")
    plan.blocks = [Block("todo", "independent", "改超时并补单测", status="running")]
    save_checkpoint(str(project), plan)
    # 助理下发时带的归属是 assistant-<单号>，项目运行时存成 sid-assistant-<单号>
    task = runner.ledger.create("dev-resume", plan.task, owner_session=f"sid-assistant-{SUFFIX}", plan_id=plan.plan_id)
    task.status, task.branch = "running", plan.branch
    assert runner.ledger.save(task)
    service = DevelopmentQuestions(runner, read_plan=lambda pid: load_plan(str(project), pid),
                                   validate_execution=lambda _: None)
    service.stage(task, 1, plan.plan_id, "todo", plan_revision(plan), "超时改成 30 秒还是 60 秒？",
                  ["30 秒", "60 秒"], "在 src/login.py 找到了超时配置")
    task.status = "blocked"
    assert runner.ledger.save(task)

    entry = dispatch.propose(str(assistant), project="VortoCode", title="改登录超时", goal="把超时改短")
    dispatch._update(str(assistant), entry["id"], lambda item: item.update(
        id=f"dsp-{SUFFIX}", status="running", remote_task_id=task.id, remote_session=f"assistant-{SUFFIX}",
        submission_target={"port": 8082, "repo_root": str(project)}))

    from src.web.server import app

    async def http(method, url, payload=None):
        path = url.split("8082", 1)[1]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            response = await client.request(method, path, json=payload, headers={"Authorization": f"Bearer {TOKEN}"})
        if response.status_code >= 400:
            raise dispatch.DispatchError(f"项目运行时返回 {response.status_code}：{response.text[:200]}")
        return response.json()
    return {"assistant": str(assistant), "runner": runner, "task": task, "http": http, "id": f"dsp-{SUFFIX}"}


@pytest.mark.asyncio
async def test_question_is_read_from_the_real_task_view_and_answered_through_the_real_route(world):
    root, http, runner, task = world["assistant"], world["http"], world["runner"], world["task"]
    await dispatch.refresh(root, http=http)
    item = dispatch.list_dispatches(root)[0]
    assert item["sync_state"] == "current", item.get("sync_error")
    assert item["status"] == "waiting"
    question = item["question"]
    assert question["question"] == "超时改成 30 秒还是 60 秒？" and question["options"] == ["30 秒", "60 秒"]
    assert question["round"] == 1 and question["id"].startswith("question-") and "src/login.py" in question["context"]
    progress = item["progress"]                                              # 真实视图里的计划进度也读得到
    assert progress["total"] == 1 and progress["steps"] == [{"title": "改超时并补单测", "status": "running"}]
    assert dispatch.progress_line(item) == "0/1 步已落地，正在做：改超时并补单测"

    answered = await dispatch.answer_question(root, world["id"], "30 秒", http=http)
    assert answered["status"] == "running" and answered["question"] is None
    await runner.join(task.id)
    assert runner.get(task.id).development["questions"][0]["answer"] == "30 秒"

    await dispatch.refresh(root, http=http)
    done = dispatch.list_dispatches(root)[0]
    assert done["status"] == "done" and "TIMEOUT=30" in done["result"] and done["branch"] == "vorto/login-timeout"


@pytest.mark.asyncio
async def test_real_route_refuses_an_answer_from_another_session(world):
    root, http = world["assistant"], world["http"]
    await dispatch.refresh(root, http=http)
    dispatch._update(root, world["id"], lambda item: item.update(remote_session="assistant-someoneelse"))
    with pytest.raises(dispatch.DispatchError, match="409"):
        await dispatch.answer_question(root, world["id"], "30 秒", http=http)
    assert world["runner"].get(world["task"].id).status == "blocked"          # 没被别人的回答推动


@pytest.mark.asyncio
async def test_approve_submits_through_the_real_route_and_the_task_belongs_to_the_ticket(world):
    root, http, runner = world["assistant"], world["http"], world["runner"]
    entry = dispatch.propose(root, project="VortoCode", title="补单测", goal="给 safe_id 补边界单测")
    sent = await dispatch.approve(root, entry["id"], http=http)
    assert sent["status"] == "running", sent.get("error")
    remote = runner.get(sent["remote_task_id"])
    assert remote is not None and remote.owner_session == "sid-assistant-" + entry["id"][4:]    # 能向你提问的前提
    assert "给 safe_id 补边界单测" in remote.prompt
    with pytest.raises(dispatch.DispatchError, match="已经下发"):            # 再点一次不会重复提交
        await dispatch.approve(root, entry["id"], http=http)
    assert len([t for t in runner.ledger.list() if t.prompt == remote.prompt]) == 1
    await runner.join(remote.id)
    await dispatch.refresh(root, http=http)
    assert {item["id"]: item["status"] for item in dispatch.list_dispatches(root)}[entry["id"]] == "done"
