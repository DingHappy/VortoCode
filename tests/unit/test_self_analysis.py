"""L1 自我分析的确定性骨架测试（离线，不打网络）。

构造一个已知形态的临时仓库，验证三类确定性分析器：
- 孤儿模块（无任何静态导入方）
- 循环依赖（import 环）
- 测试缺口（无测试导入）
并同时覆盖【相对导入】与【绝对导入】两条解析路径。
"""

import pytest

from src.orchestrator.self_analysis import analyze_self, render_report


def _make_repo(root):
    src = root / "src"
    tests = root / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "__init__.py").write_text("")
    # core：被 app 相对导入；app 被测试导入 -> core 经传递闭包被测试触及 -> 非缺口
    (src / "core.py").write_text("def helper():\n    return 1\n")
    # app：相对导入 core 与 cycle_a；被 test_app 绝对导入 -> 非孤儿、非缺口
    (src / "app.py").write_text(
        "from .core import helper\n"
        "from .cycle_a import a_fn\n\n"
        "def run():\n    return helper() + a_fn()\n"
    )
    # loner：无人导入、名字也不在语料里 -> 孤儿
    (src / "loner.py").write_text("X = 1\n")
    # cycle_a <-> cycle_b：循环依赖（相对导入）；二者经 app 被测试触及
    (src / "cycle_a.py").write_text("from .cycle_b import b_fn\n\ndef a_fn():\n    return 1\n")
    (src / "cycle_b.py").write_text("from .cycle_a import a_fn\n\ndef b_fn():\n    return 2\n")
    # feature 孤岛：被 feature_user 使用，但 feature_user 无人导入、测试也到不了 -> feature 是测试缺口
    (src / "feature.py").write_text("def feat():\n    return 9\n")
    (src / "feature_user.py").write_text("from .feature import feat\n\ndef use():\n    return feat()\n")
    # 测试只导入 app（绝对导入路径）
    (tests / "__init__.py").write_text("")
    (tests / "test_app.py").write_text("from src.app import run\n\n\ndef test_run():\n    assert run() == 2\n")


@pytest.mark.asyncio
async def test_detects_orphan_cycle_and_test_gap(tmp_path):
    _make_repo(tmp_path)

    report = await analyze_self(str(tmp_path))  # 不传 llm_paths -> 纯确定性

    by_cat = {}
    for f in report.findings:
        by_cat.setdefault(f.category, []).append(f)

    # 孤儿：loner 与 feature_user（均无人导入）；core/app/cycle_* 不应误报
    orphan_titles = " ".join(f.title for f in by_cat.get("orphan-module", []))
    assert "src.loner" in orphan_titles
    assert "src.feature_user" in orphan_titles
    assert "src.core" not in orphan_titles      # core 被 app 相对导入，非孤儿
    assert "src.app" not in orphan_titles        # app 被测试绝对导入，非孤儿

    # 循环依赖：cycle_a <-> cycle_b
    cycle_titles = " ".join(f.title for f in by_cat.get("circular-dependency", []))
    assert "cycle_a" in cycle_titles and "cycle_b" in cycle_titles

    # 测试缺口（传递闭包语义）：feature 是测试到不了的孤岛 -> 缺口；
    # core/app/cycle_* 经 test_app 传递可达 -> 不报；孤儿不重复计入
    gap_titles = " ".join(f.title for f in by_cat.get("test-gap", []))
    assert "src.feature" in gap_titles
    assert "src.core" not in gap_titles          # 经 test->app->core 传递可达
    assert "src.app" not in gap_titles
    assert "src.cycle_a" not in gap_titles       # 经 test->app->cycle_a 传递可达
    assert "src.loner" not in gap_titles         # 孤儿不重复计入测试缺口


@pytest.mark.asyncio
async def test_detects_undeclared_dependency(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "__init__.py").write_text("")
    (tmp_path / "requirements.txt").write_text("fastapi>=0.100\n")
    # requests：未声明、未保护 -> 报；numpy：未声明但 try/except 保护 -> 不报；
    # fastapi：已声明 -> 不报；os：标准库 -> 不报
    (src / "app.py").write_text(
        "import os\n"
        "import requests\n"
        "import fastapi\n"
        "try:\n"
        "    import numpy\n"
        "except ImportError:\n"
        "    numpy = None\n"
    )

    report = await analyze_self(str(tmp_path))
    undeclared = {f.title.split("：")[-1] for f in report.findings
                  if f.category == "undeclared-dependency"}

    assert "requests" in undeclared
    assert "numpy" not in undeclared       # try/except 保护 -> 视为可选，不报
    assert "fastapi" not in undeclared     # requirements 已声明
    assert "os" not in undeclared          # 标准库


@pytest.mark.asyncio
async def test_report_renders_and_declares_skips(tmp_path):
    _make_repo(tmp_path)

    report = await analyze_self(str(tmp_path))
    text = render_report(report)

    assert "自我分析报告" in text
    assert report.stats["modules_scanned"] >= 5
    # 未指定 --paths 时，应明确声明 LLM 深审被跳过（no silent caps）
    assert any("LLM 深审" in s for s in report.skipped)


@pytest.mark.asyncio
async def test_clean_repo_has_no_structural_findings(tmp_path):
    """一个干净的小仓库不应产生孤儿/循环误报（测试缺口允许存在）。"""
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "__init__.py").write_text("")
    (src / "util.py").write_text("def f():\n    return 1\n")
    (tests / "__init__.py").write_text("")
    (tests / "test_util.py").write_text("from src.util import f\n\n\ndef test_f():\n    assert f() == 1\n")

    report = await analyze_self(str(tmp_path))
    cats = {f.category for f in report.findings}

    assert "orphan-module" not in cats
    assert "circular-dependency" not in cats
    assert "test-gap" not in cats     # util 被 test_util 导入


@pytest.mark.asyncio
async def test_deferred_import_cycle_is_not_reported(tmp_path):
    """**函数内延迟导入不算环**——那正是打破环的标准手法。

    2026-08-03：本仓 4 条 circular-dependency 全是这种假警报（逐条查完无一可动），
    工具反过来在指控解法本身。一个会喊狼来了的检测比没有检测更糟：
    真问题会被淹在长期噪音里。
    """
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "__init__.py").write_text("")
    # a 在**模块级**导入 b；b 只在**函数体内**导入 a → 导入期无环
    (src / "a.py").write_text("from .b import b_fn\n\n\ndef a_fn():\n    return b_fn()\n")
    (src / "b.py").write_text(
        "def b_fn():\n"
        "    from .a import a_fn      # 延迟导入：调用时才解析，不构成导入期的环\n"
        "    return 1\n"
    )
    (tests / "__init__.py").write_text("")
    (tests / "test_a.py").write_text("from src.a import a_fn\n\n\ndef test_a():\n    assert a_fn() == 1\n")

    report = await analyze_self(str(tmp_path))
    cycles = [f for f in report.findings if f.category == "circular-dependency"]
    assert not cycles, f"延迟导入被误报成环: {[f.title for f in cycles]}"


@pytest.mark.asyncio
async def test_module_level_cycle_is_still_reported(tmp_path):
    """反面：**真环仍要报**。

    没有这一条，上面那个修复就可能是"把检测关掉"而不是"让它更准"——
    两者在报告上都表现为 0 条问题。
    """
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "__init__.py").write_text("")
    (src / "x.py").write_text("from .y import y_fn\n\n\ndef x_fn():\n    return 1\n")
    (src / "y.py").write_text("from .x import x_fn\n\n\ndef y_fn():\n    return 2\n")
    (tests / "__init__.py").write_text("")
    (tests / "test_x.py").write_text("from src.x import x_fn\n\n\ndef test_x():\n    assert x_fn() == 1\n")

    report = await analyze_self(str(tmp_path))
    cycles = [f for f in report.findings if f.category == "circular-dependency"]
    assert cycles, "模块级真环没报出来——检测被关掉了，不是变准了"


@pytest.mark.asyncio
async def test_module_level_try_import_still_counts_as_a_real_edge(tmp_path):
    """模块级 try/except ImportError 里的导入**照样在导入期执行**，是真边。

    判据是"在不在函数里"，不是"在不在最外层"——写成后者会漏掉这类常见写法。
    """
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "__init__.py").write_text("")
    (src / "p.py").write_text(
        "try:\n    from .q import q_fn\nexcept ImportError:\n    q_fn = None\n\n\n"
        "def p_fn():\n    return 1\n"
    )
    (src / "q.py").write_text("from .p import p_fn\n\n\ndef q_fn():\n    return 2\n")
    (tests / "__init__.py").write_text("")
    (tests / "test_p.py").write_text("from src.p import p_fn\n\n\ndef test_p():\n    assert p_fn() == 1\n")

    report = await analyze_self(str(tmp_path))
    cycles = [f for f in report.findings if f.category == "circular-dependency"]
    assert cycles, "模块级 try-import 的环被漏掉了"
