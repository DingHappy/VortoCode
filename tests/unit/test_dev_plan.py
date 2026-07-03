"""C1 · dev_auto 计划持久化 + 断点续跑（src/agents/dev_plan.py + build_dev_tools 的 dev_resume）。

不真跑流水线：monkeypatch worktree 层的实现/落分支/验证，聚焦验证
①计划文件 schema 往返 ②write-ahead 时序（崩溃不超前标 landed）③dev_resume 跳过已 landed、
只重跑未完成 ④全 landed 时幂等（只重验集成）⑤手改计划文件后按改后的跑。
"""
import json
import subprocess

import pytest

from src.agents import dev_plan as dp
from src.agents import main_agent as ma


# --------------------------------------------------------------------- 纯数据：schema 往返 / 落盘
def test_devplan_roundtrip(tmp_path):
    plan = dp.DevPlan.new("大任务", "vorto/auto-x", "main", test_sel="tests/", want_pr=True)
    plan.blocks.append(dp.Block(id="ind-0", kind="independent", desc="做甲"))
    plan.blocks.append(dp.Block(id="d1", kind="dependent", desc="做乙", title="乙", deps=["ind-0"]))
    plan.satisfied_ids = ["ind-0"]
    assert dp.save_plan(str(tmp_path), plan) is True

    got = dp.load_plan(str(tmp_path), plan.plan_id)
    assert got is not None
    assert got.task == "大任务" and got.branch == "vorto/auto-x" and got.want_pr is True
    assert [b.id for b in got.blocks] == ["ind-0", "d1"]
    assert got.block("d1").deps == ["ind-0"] and got.block("d1").title == "乙"
    assert got.updated                                    # 落盘刷新了时间戳


def test_load_plan_missing_and_bad(tmp_path):
    assert dp.load_plan(str(tmp_path), "nope") is None
    p = tmp_path / ".vortocode" / "dev_plans" / "broken.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    assert dp.load_plan(str(tmp_path), "broken") is None


def test_plan_id_sanitized_against_traversal(tmp_path):
    plan = dp.DevPlan.new("t", "b", "main", plan_id="../../etc/passwd")
    assert dp.save_plan(str(tmp_path), plan) is True
    # 清洗后落在 dev_plans 目录内，不逃逸
    files = list((tmp_path / ".vortocode" / "dev_plans").glob("*.json"))
    assert files and all(f.parent.name == "dev_plans" for f in files)


def test_from_dict_tolerates_unknown_keys(tmp_path):
    d = {"plan_id": "p1", "task": "t", "branch": "b", "base": "main",
         "blocks": [{"id": "x", "kind": "independent", "desc": "d", "future_field": 9}],
         "unknown_top": 1}
    plan = dp.DevPlan.from_dict(d)                         # 向前兼容：多余字段不炸
    assert plan.plan_id == "p1" and plan.blocks[0].id == "x"


def test_list_plans_sorted(tmp_path):
    a = dp.DevPlan.new("任务A", "vorto/a", "main")
    dp.save_plan(str(tmp_path), a)
    b = dp.DevPlan.new("任务B", "vorto/b", "main")
    dp.save_plan(str(tmp_path), b)
    listed = dp.list_plans(str(tmp_path))
    assert {p["plan_id"] for p in listed} == {a.plan_id, b.plan_id}
    assert all("summary" in p and "status" in p for p in listed)


# --------------------------------------------------------------------- 编排：write-ahead + resume
def _init_repo(tmp_path):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "f.txt").write_text("x\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "init")


class _Sub:
    def __init__(self, sid, title="", deps=None):
        self.id = sid
        self.title = title
        self.description = title or sid
        self.dependencies = deps or []
        self.acceptance_criteria = []


def _dev_tools(tmp_path, on_progress=None):
    return {t.name: t for t in ma.build_dev_tools(str(tmp_path), on_progress=on_progress)}


@pytest.mark.asyncio
async def test_write_ahead_never_marks_landed_before_run(tmp_path, monkeypatch):
    """崩溃在第 2 个依赖块 → 计划文件如实：块1 landed、块2 running（不超前标 landed）、块3 pending。"""
    _init_repo(tmp_path)
    import src.agents.decompose as dec
    import src.agents.worktree as wt

    async def fake_decompose(task):
        # 1 个独立 + 3 个依赖（链式），确保依赖阶段逐个 write-ahead
        deferred = [_Sub("d1", "D1", ["ind"]), _Sub("d2", "D2", ["d1"]), _Sub("d3", "D3", ["d2"])]
        return {"descriptions": ["独立块"], "independent": [_Sub("ind", "IND")],
                "deferred": deferred, "total": 4}
    monkeypatch.setattr(dec, "decompose_for_parallel", fake_decompose)

    async def ok_isolated(repo, wid, desc, build, test_cmd=None):
        return ("diff\n", "c", {"ok": True, "output": ""})
    monkeypatch.setattr(wt, "run_isolated_task", ok_isolated)
    # 独立批落分支：真建分支（让 _branch_exists 之后为真），返回全 applied
    monkeypatch.setattr(wt, "apply_diffs_to_branch",
                        lambda repo, br, items, tc=None: (
                            subprocess.run(["git", "-C", str(repo), "branch", br, "HEAD"],
                                           capture_output=True),
                            {"ok": True, "branch": br, "applied": [m for _d, m in items],
                             "failed": [], "integration": None})[1])

    calls = {"n": 0}

    async def dep_then_crash(repo, wid, branch, desc, build, msg, test_cmd=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": True, "conclusion": "c", "output": ""}     # 块1 成功
        raise RuntimeError("模拟进程被 kill")                          # 块2 崩溃
    monkeypatch.setattr(wt, "run_dependent_on_branch", dep_then_crash)

    tool = _dev_tools(tmp_path)["dev_auto"]
    with pytest.raises(RuntimeError):
        await tool.handler({"task": "大任务"})

    # 从磁盘读回计划，断言状态如实、无超前标记
    plans = dp.list_plans(str(tmp_path))
    assert plans, "计划应已 write-ahead 落盘"
    plan = dp.load_plan(str(tmp_path), plans[0]["plan_id"])
    by = {b.id: b for b in plan.blocks}
    assert by["ind-0"].status == "landed"                 # 独立块已落地（合成 id ind-0）
    assert by["d1"].status == "landed"                    # 依赖块1 成功落地
    assert by["d2"].status == "running"                   # 块2 崩溃时停在 running（不是 landed！）
    assert by["d3"].status == "pending"                   # 块3 从没跑过
    assert by["d2"].status != "landed" and by["d3"].status != "landed"   # 铁律：不超前标 landed


@pytest.mark.asyncio
async def test_resume_skips_landed_reruns_unfinished(tmp_path, monkeypatch):
    """dev_resume：已 landed 的块不再实现，只补跑未完成的；分支已在则在其上接力。"""
    _init_repo(tmp_path)
    import src.agents.worktree as wt

    # 预置一个"跑了一半"的计划：ind-0 landed、ind-1 failed、d1 pending；分支真存在
    branch = "vorto/auto-half"
    subprocess.run(["git", "-C", str(tmp_path), "branch", branch, "HEAD"], capture_output=True)
    plan = dp.DevPlan.new("续跑任务", branch, "main")
    plan.blocks = [
        dp.Block(id="ind-0", kind="independent", desc="已完成甲", status="landed"),
        dp.Block(id="ind-1", kind="independent", desc="没完成乙", status="failed"),
        dp.Block(id="d1", kind="dependent", desc="依赖丙", title="丙", status="pending", deps=["ind-0"]),
    ]
    plan.satisfied_ids = ["ind-0", "ind-1"]
    dp.save_plan(str(tmp_path), plan)

    implemented = []

    async def track_dep(repo, wid, branch_, desc, build, msg, test_cmd=None):
        implemented.append(desc)
        return {"ok": True, "conclusion": "c", "output": ""}
    monkeypatch.setattr(wt, "run_dependent_on_branch", track_dep)

    async def no_isolated(*a, **k):
        raise AssertionError("resume 分支已存在时不应走 run_isolated_task")
    monkeypatch.setattr(wt, "run_isolated_task", no_isolated)
    monkeypatch.setattr(wt, "verify_branch",
                        lambda repo, br, tc, wid: {"ok": True, "output": "", "cmd": "p"})

    out = await _dev_tools(tmp_path)["dev_resume"].handler({"plan_id": plan.plan_id})

    assert "已完成甲" not in implemented                   # 已 landed → 跳过
    assert "没完成乙" in implemented                        # 未完成的独立块 → 补跑
    assert "依赖丙" in implemented                          # 依赖块 → 接力
    assert "集成后全量测试通过" in out
    reloaded = dp.load_plan(str(tmp_path), plan.plan_id)
    assert all(b.status == "landed" for b in reloaded.blocks)   # 续跑后全部落地
    assert reloaded.status == "integrated"


@pytest.mark.asyncio
async def test_resume_all_landed_is_idempotent_only_reverifies(tmp_path, monkeypatch):
    """全部块已 landed → resume 不再实现任何块，只重跑一次集成验证（幂等）。"""
    _init_repo(tmp_path)
    import src.agents.worktree as wt

    branch = "vorto/auto-done"
    subprocess.run(["git", "-C", str(tmp_path), "branch", branch, "HEAD"], capture_output=True)
    plan = dp.DevPlan.new("已完成任务", branch, "main")
    plan.blocks = [dp.Block(id="ind-0", kind="independent", desc="甲", status="landed"),
                   dp.Block(id="d1", kind="dependent", desc="乙", status="landed")]
    plan.status = "integrated"
    dp.save_plan(str(tmp_path), plan)

    async def no_dep(*a, **k):
        raise AssertionError("全 landed 不应再实现")
    monkeypatch.setattr(wt, "run_dependent_on_branch", no_dep)
    monkeypatch.setattr(wt, "run_isolated_task",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("全 landed 不应再实现")))
    verify_calls = {"n": 0}

    def track_verify(repo, br, tc, wid):
        verify_calls["n"] += 1
        return {"ok": True, "output": "", "cmd": "p"}
    monkeypatch.setattr(wt, "verify_branch", track_verify)

    out = await _dev_tools(tmp_path)["dev_resume"].handler({"plan_id": plan.plan_id})
    assert verify_calls["n"] == 1                          # 只重验一次集成
    assert "集成后全量测试通过" in out


@pytest.mark.asyncio
async def test_resume_honors_hand_edited_plan(tmp_path, monkeypatch):
    """手改计划文件（删块 + 改描述）后 resume 按**改后的**跑。"""
    _init_repo(tmp_path)
    import src.agents.worktree as wt

    branch = "vorto/auto-edit"
    subprocess.run(["git", "-C", str(tmp_path), "branch", branch, "HEAD"], capture_output=True)
    plan = dp.DevPlan.new("原任务", branch, "main")
    plan.blocks = [dp.Block(id="ind-0", kind="independent", desc="原描述甲", status="pending"),
                   dp.Block(id="ind-1", kind="independent", desc="原描述乙", status="pending")]
    dp.save_plan(str(tmp_path), plan)

    # 人手改文件：删掉 ind-1、把 ind-0 的描述改掉
    path = tmp_path / ".vortocode" / "dev_plans" / f"{plan.plan_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["blocks"] = [b for b in data["blocks"] if b["id"] == "ind-0"]
    data["blocks"][0]["desc"] = "手改后的新描述"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    implemented = []

    async def track_dep(repo, wid, branch_, desc, build, msg, test_cmd=None):
        implemented.append(desc)
        return {"ok": True, "conclusion": "c", "output": ""}
    monkeypatch.setattr(wt, "run_dependent_on_branch", track_dep)
    monkeypatch.setattr(wt, "verify_branch",
                        lambda repo, br, tc, wid: {"ok": True, "output": "", "cmd": "p"})

    await _dev_tools(tmp_path)["dev_resume"].handler({"plan_id": plan.plan_id})
    assert implemented == ["手改后的新描述"]                # 只跑改后留下的那一块、用新描述


@pytest.mark.asyncio
async def test_resume_lists_when_no_id_and_errors_on_unknown(tmp_path):
    _init_repo(tmp_path)
    tool = _dev_tools(tmp_path)["dev_resume"]
    # 没有任何计划
    assert "没有可续跑的计划" in await tool.handler({})
    # 有计划但不给 id → 列出
    plan = dp.DevPlan.new("列我", "vorto/x", "main")
    dp.save_plan(str(tmp_path), plan)
    listed = await tool.handler({})
    assert "需要 plan_id" in listed and plan.plan_id in listed
    # 未知 id → 报错
    assert "找不到计划" in await tool.handler({"plan_id": "nonexistent"})


@pytest.mark.asyncio
async def test_dev_auto_persists_plan_and_reports_id(tmp_path, monkeypatch):
    """fresh dev_auto：分解后即落盘计划、输出带 plan_id，跑完 status=integrated。"""
    _init_repo(tmp_path)
    import src.agents.decompose as dec
    import src.agents.worktree as wt

    async def fake_decompose(task):
        return {"descriptions": ["块甲"], "independent": [_Sub("a", "A")], "deferred": [], "total": 1}
    monkeypatch.setattr(dec, "decompose_for_parallel", fake_decompose)

    async def ok_isolated(repo, wid, desc, build, test_cmd=None):
        return ("diff\n", "c", {"ok": True, "output": ""})
    monkeypatch.setattr(wt, "run_isolated_task", ok_isolated)
    monkeypatch.setattr(wt, "apply_diffs_to_branch",
                        lambda repo, br, items, tc=None: {"ok": True, "branch": br,
                                                          "applied": [m for _d, m in items],
                                                          "failed": [], "integration": None})
    monkeypatch.setattr(wt, "verify_branch",
                        lambda repo, br, tc, wid: {"ok": True, "output": "", "cmd": "p"})

    out = await _dev_tools(tmp_path)["dev_auto"].handler({"task": "做个事"})
    assert "plan_id=" in out and "dev_resume" in out
    plans = dp.list_plans(str(tmp_path))
    assert len(plans) == 1 and plans[0]["status"] == "integrated"
