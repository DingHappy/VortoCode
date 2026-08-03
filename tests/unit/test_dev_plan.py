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


def _porcelain(repo):
    return subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                          capture_output=True, text=True).stdout


def test_save_plan_leaves_worktree_clean(tmp_path):
    """save_plan 落 .vortocode/dev_plans/ 后，目标仓库（无自带 .gitignore）git status 仍干净——
    工具生成态经 .vortocode/.gitignore 自忽略，不污染用户工作区（评测 dependency_chain clean=False 根治）。"""
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    (tmp_path / "mod.py").write_text("x=1\n", encoding="utf-8")
    git("add", "-A"); git("commit", "-q", "-m", "init")
    assert _porcelain(tmp_path).strip() == ""            # 起点干净

    plan = dp.DevPlan.new("任务", "vorto/auto-x", "main")
    plan.blocks = [dp.Block(id="d1", kind="dependent", desc="做甲")]
    dp.save_plan(str(tmp_path), plan)
    assert (tmp_path / ".vortocode" / "dev_plans").is_dir()      # 计划确实落了盘
    assert (tmp_path / ".vortocode" / ".gitignore").is_file()    # 自忽略清单已放
    assert _porcelain(tmp_path).strip() == ""            # .vortocode/ 生成态对 git status 隐形


def test_state_gitignore_keeps_user_config_versionable(tmp_path):
    """自忽略清单只挡生成态；用户配置（permissions.yaml 等）仍可 git add。"""
    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True, text=True)
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    (tmp_path / "mod.py").write_text("x=1\n", encoding="utf-8"); git("add", "-A"); git("commit", "-qm", "init")
    dp.ensure_state_gitignore(str(tmp_path))
    (tmp_path / ".vortocode" / "dev_plans").mkdir(parents=True)
    (tmp_path / ".vortocode" / "dev_plans" / "x.json").write_text("{}", encoding="utf-8")   # 生成态
    (tmp_path / ".vortocode" / "permissions.yaml").write_text("deny: []", encoding="utf-8")  # 用户配置
    git("add", ".vortocode/")
    staged = git("diff", "--cached", "--name-only").stdout
    assert "permissions.yaml" in staged and "dev_plans" not in staged


def test_ensure_state_gitignore_upgrades_legacy_and_keeps_custom(tmp_path):
    """旧版无标记文件缺托管条目 → 追加托管区（老仓库也能收到清单升级，#140 评审）；
    用户自定义行逐字保留。"""
    d = tmp_path / ".vortocode"; d.mkdir()
    (d / ".gitignore").write_text("custom\nworktrees/\n", encoding="utf-8")   # 旧清单：缺新条目
    dp.ensure_state_gitignore(str(tmp_path))
    content = (d / ".gitignore").read_text(encoding="utf-8")
    assert content.startswith("custom\nworktrees/\n")             # 用户自定义逐字保留
    assert "tui_history" in content and "notices.jsonl" in content  # 缺的托管条目补上了
    assert dp._MANAGED_BEGIN in content                           # 已迁到托管区（下次幂等升级）


def test_ensure_state_gitignore_legacy_fully_covered_untouched(tmp_path):
    """旧版无标记文件但托管条目**全齐**（用户可能删过注释/改过顺序）→ 一字不动（grandfather）。"""
    d = tmp_path / ".vortocode"; d.mkdir()
    legacy = "\n".join(["# 我的注释"] + dp._STATE_ENTRIES) + "\n"
    (d / ".gitignore").write_text(legacy, encoding="utf-8")
    dp.ensure_state_gitignore(str(tmp_path))
    assert (d / ".gitignore").read_text(encoding="utf-8") == legacy


def test_ensure_state_gitignore_managed_block_idempotent_upgrade(tmp_path):
    """标记区内容过期 → 原地重写补齐；区外（用户前后自定义）原样保留；再跑一遍零改动（幂等）。"""
    d = tmp_path / ".vortocode"; d.mkdir()
    stale = (f"pre-custom\n{dp._MANAGED_BEGIN}\nworktrees/\n{dp._MANAGED_END}\npost-custom\n")
    (d / ".gitignore").write_text(stale, encoding="utf-8")
    dp.ensure_state_gitignore(str(tmp_path))
    content = (d / ".gitignore").read_text(encoding="utf-8")
    assert content.startswith("pre-custom\n") and content.rstrip().endswith("post-custom")
    assert "tui_history" in content and "notices.jsonl" in content
    dp.ensure_state_gitignore(str(tmp_path))                      # 幂等：第二遍无变化
    assert (d / ".gitignore").read_text(encoding="utf-8") == content


def test_list_plans_sorted(tmp_path):
    a = dp.DevPlan.new("任务A", "vorto/a", "main")
    dp.save_plan(str(tmp_path), a)
    b = dp.DevPlan.new("任务B", "vorto/b", "main")
    dp.save_plan(str(tmp_path), b)
    listed = dp.list_plans(str(tmp_path))
    assert {p["plan_id"] for p in listed} == {a.plan_id, b.plan_id}
    assert all("summary" in p and "status" in p for p in listed)


def test_format_plan_list_and_detail(tmp_path):
    plan = dp.DevPlan.new("实现任务面板", "vorto/tasks", "main", plan_id="task-panel")
    plan.blocks = [
        dp.Block(id="ind-0", kind="independent", desc="已完成块", status="landed"),
        dp.Block(id="dep-1", kind="dependent", desc="待续跑块", title="续跑块", status="failed",
                 deps=["ind-0"], attempts=2, note="测试失败"),
    ]
    plan.integration = {"ok": False, "cmd": "pytest"}
    dp.save_plan(str(tmp_path), plan)
    plans = dp.list_plans(str(tmp_path))

    listed = dp.format_plan_list(plans)
    detail = dp.format_plan_detail(dp.load_plan(str(tmp_path), "task-panel"))

    assert "task-panel" in listed
    assert "vorto/tasks" in listed
    assert "Dev 计划详情: task-panel" in detail
    assert "进度 1/2 landed" in detail
    assert "dep-1 [dependent] failed deps=ind-0 attempts=2" in detail
    assert "dev_resume(plan_id=task-panel)" in detail


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
                        lambda repo, br, tc, wid, *a, **k: {"ok": True, "output": "", "cmd": "p"})

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

    def track_verify(repo, br, tc, wid, *a, **k):
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
                        lambda repo, br, tc, wid, *a, **k: {"ok": True, "output": "", "cmd": "p"})

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
                        lambda repo, br, tc, wid, *a, **k: {"ok": True, "output": "", "cmd": "p"})

    out = await _dev_tools(tmp_path)["dev_auto"].handler({"task": "做个事"})
    assert "plan_id=" in out and "dev_resume" in out
    plans = dp.list_plans(str(tmp_path))
    assert len(plans) == 1 and plans[0]["status"] == "integrated"


@pytest.mark.asyncio
async def test_dev_auto_honors_pinned_plan_id(tmp_path, monkeypatch):
    """dev_auto 接受调用方指定的 plan_id——后台 worker 靠它按确定 id load_plan 拿 branch，
    不必猜"全局最新 plan"（防并发多任务串单，#128 评审）。"""
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
                        lambda repo, br, tc, wid, *a, **k: {"ok": True, "output": "", "cmd": "p"})

    await _dev_tools(tmp_path)["dev_auto"].handler({"task": "做个事", "plan_id": "bg-task-xyz"})
    loaded = dp.load_plan(str(tmp_path), "bg-task-xyz")       # 按指定 id 精确取回本次计划
    assert loaded is not None and loaded.plan_id == "bg-task-xyz"
    assert loaded.branch.startswith("vorto/auto-") and loaded.status == "integrated"


@pytest.mark.asyncio
async def test_apply_whole_failure_never_marks_landed(tmp_path, monkeypatch):
    """整体落分支失败（worktree add 挂）→ 绿块**不得**被误标 landed（#127 P1 回归）。

    apply_diffs_to_branch 整体失败返回 applied=[]、failed=[{"msg":"(worktree add)"}]——绿块 msg 既不在
    applied 也不在 failed，'不在 failed 就 landed' 的反推法会把它们全误标 landed，污染计划、令 dev_resume
    跳过实际没落地的块。修法：只以 applied 白名单判 landed。
    """
    _init_repo(tmp_path)
    import src.agents.decompose as dec
    import src.agents.worktree as wt

    async def fake_decompose(task):
        return {"descriptions": ["块甲", "块乙"], "independent": [], "deferred": [], "total": 2}
    monkeypatch.setattr(dec, "decompose_for_parallel", fake_decompose)

    async def ok_isolated(repo, wid, desc, build, test_cmd=None):
        return ("diff\n", "c", {"ok": True, "output": ""})       # 两块都自测绿
    monkeypatch.setattr(wt, "run_isolated_task", ok_isolated)
    # 整体 apply 失败（worktree add 挂）：applied 空、failed 只含 "(worktree add)"
    monkeypatch.setattr(wt, "apply_diffs_to_branch",
                        lambda repo, br, items, tc=None: {
                            "ok": False, "branch": br, "applied": [],
                            "failed": [{"msg": "(worktree add)", "error": "fatal: already exists"}],
                            "integration": None})
    verified = {"n": 0}
    monkeypatch.setattr(wt, "verify_branch",
                        lambda repo, br, tc, wid, *a, **k: verified.__setitem__("n", verified["n"] + 1)
                        or {"ok": True, "output": "", "cmd": "p"})

    out = await _dev_tools(tmp_path)["dev_auto"].handler({"task": "做两件事"})
    plan = dp.load_plan(str(tmp_path), dp.list_plans(str(tmp_path))[0]["plan_id"])
    assert all(b.status == "failed" for b in plan.blocks)         # 绝不误标 landed
    assert plan.status == "failed"                               # 无任何落地 → 计划整体失败
    assert verified["n"] == 0                                    # 没落地就不该跑集成验证
    assert "没有任何子任务落地" in out


# ---------------------------------------------------------------- 无改动时要说清为什么
def test_noop_note_carries_the_subagent_reason():
    """子 agent 没动手时，把**它自己说的原因**带给人。

    真机代价（2026-07-24/25）：同一个「给 README 补一行」的任务在 14 小时里重试了 8 次，
    每次拿到的都只有「无改动/出错」五个字。子 agent 大概率每次都说了原因，全被扔在
    `diff, _c, ver = ...` 那个下划线里。人只能盲改提示词再试——最终成功的两次，
    正是把指令改得极其具体之后。
    """
    from src.agents.main_agent import _noop_note

    note = _noop_note("README.md 里没有找到叫「核心特性清单」的段落，无法定位插入位置。")
    assert "核心特性清单" in note, "原因没带出来，人还是拿不到线索"
    assert "无改动" in note


def test_noop_note_says_so_when_there_is_no_reason():
    """连结论都没有时**如实说**——别让人以为原因被吞了。"""
    from src.agents.main_agent import _noop_note

    for empty in (None, "", "   "):
        assert "没说明原因" in _noop_note(empty)


def test_noop_note_is_bounded():
    """子 agent 可能长篇大论；台账里的 note 不该被一次失败撑爆。"""
    from src.agents.main_agent import _noop_note

    assert len(_noop_note("很长的解释" * 500)) < 400


@pytest.mark.asyncio
async def test_dependent_failure_preserves_conclusion_in_note(tmp_path, monkeypatch):
    """接力块失败时 note 应带子 agent 的 conclusion（回归：曾丢掉原因只留 output 尾巴）。"""
    _init_repo(tmp_path)
    import src.agents.worktree as wt

    plan = dp.DevPlan.new("大任务", "vorto/auto-note", "main")
    plan.blocks = [
        dp.Block(id="ind-0", kind="independent", desc="甲", status="landed", note="ok"),
        dp.Block(id="d1", kind="dependent", desc="乙", deps=["ind-0"]),
    ]
    plan.satisfied_ids = ["ind-0"]
    dp.save_plan(str(tmp_path), plan)

    async def fail_dep(repo, wid, branch_, desc, build, msg, test_cmd=None):
        return {"ok": False, "conclusion": "类型不兼容：Foo 没有 bar 方法", "output": "raw stderr..."}
    monkeypatch.setattr(wt, "run_dependent_on_branch", fail_dep)
    monkeypatch.setattr(wt, "apply_diffs_to_branch", lambda *a, **k: "")

    await _dev_tools(tmp_path)["dev_resume"].handler({"plan_id": plan.plan_id})

    loaded = dp.load_plan(str(tmp_path), plan.plan_id)
    d1 = loaded.block("d1")
    assert d1 is not None and d1.status == "failed"
    assert "Foo 没有 bar 方法" in d1.note, (
        f"接力块失败时 note 应带 conclusion，实际 note={d1.note!r}")
    # 【人工修正 2026-08-03】本条原为 `assert "raw stderr" not in d1.note`（agent 写的）。
    # 那个假设是"output 是垃圾、conclusion 才有信息"，但真实出口里反过来：测试红时
    # output 是 1500 字的失败尾部（哪个测试红、为什么），是**机器真相**；conclusion 是
    # 子 agent 的说法。两者各有各的用，所以 _fail_note 两个都带——改断言为"都在"。
    assert "raw stderr" in d1.note, "output 是真死因（哪个测试红），不能扔"


# ---------------------------------------------------------------- 失败原因要按真实死因分流
def test_fail_note_routes_by_actual_cause():
    """接力块失败时**按真实死因分流**，别一律说成「无改动」。

    run_dependent_on_branch 有五个失败出口，只有一个是无改动。2026-08-03 我让 agent
    修「接力块丢原因」，它把所有失败都改成 _noop_note(conclusion)——测试红时那 1500 字
    失败尾部被换成一句错误的「无改动」，**比原来还糟**（原来至少留了 output 尾巴）。
    我当时合得太快。这条钉住分流。
    """
    from src.agents.main_agent import _fail_note

    # 测试红：要的是测试输出，不是"无改动"
    note = _fail_note({"ok": False, "conclusion": "我改完了",
                       "output": "FAILED tests/unit/test_x.py::test_y\nAssertionError: 期望 3 实际 2"})
    assert "test_y" in note, "测试失败尾部被扔了——那是唯一能定位问题的东西"
    assert "无改动" not in note, "有改动却被标成无改动，把人引向错误方向"

    # 真的无改动：带子 agent 的原因
    note = _fail_note({"ok": False, "conclusion": "README 里没有那个段落",
                       "output": "无改动（子 agent 未修改任何文件）"})
    assert "README 里没有那个段落" in note

    # 通道故障：带 output（那是真死因）
    note = _fail_note({"ok": False, "conclusion": "", "output": "LLM 通道故障: 502 Bad Gateway"})
    assert "502" in note

    # 什么都没有：如实说，别装作知道
    assert "没说明原因" in _fail_note({"ok": False, "conclusion": "", "output": ""})


def test_fail_note_is_bounded():
    """一次失败不该把台账撑爆。"""
    from src.agents.main_agent import _fail_note

    assert len(_fail_note({"output": "x" * 5000})) <= 400
