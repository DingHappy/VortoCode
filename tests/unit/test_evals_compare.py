"""B3 · 评测基线对比 + 矩阵解析（evals/compare.py）——纯函数，不触模型。"""
import json

from evals import compare


def _report(model="mimo-v2.5", protocol="native", scenarios=None, aggregate=None):
    return {
        "meta": {"model": model, "protocol": protocol},
        "aggregate": aggregate or {"pass_rate": 1.0, "honesty_rate": 1.0, "landing_rate": 1.0,
                                   "clean_rate": 1.0, "n": 3, "landing_n": 3, "honesty_n": 3},
        "scenarios": scenarios or [],
        "skipped": [],
    }


def _scn(name, passed, run=1):
    # 带齐 to_markdown 渲染要读的字段（compare 只看 name/passed，其余给默认值）
    return {"name": name, "run": run, "passed": passed, "landed": passed, "honest": True,
            "clean": True, "expect_land": True, "land_ok": passed, "surfaced": False,
            "honest_reason": "standard", "duration_s": 1, "message_excerpt": ""}


# --------------------------------------------------------------------- 矩阵解析
def test_parse_matrix_cross_product():
    assert compare.parse_matrix("m1,m2 × native,prompt") == [
        ("m1", "native"), ("m1", "prompt"), ("m2", "native"), ("m2", "prompt")]


def test_parse_matrix_lowercase_x_and_no_protocol():
    assert compare.parse_matrix("mimo-v2.5,mimo-v2.5-pro x native") == [
        ("mimo-v2.5", "native"), ("mimo-v2.5-pro", "native")]
    assert compare.parse_matrix("m1,m2") == [("m1", None), ("m2", None)]
    assert compare.parse_matrix("") == []


# --------------------------------------------------------------------- 通过率聚合
def test_pass_rate_by_scenario_averages_runs():
    rep = _report(scenarios=[_scn("a", True, 1), _scn("a", False, 2), _scn("b", True, 1)])
    rates = compare.pass_rate_by_scenario(rep)
    assert rates["a"] == 0.5 and rates["b"] == 1.0


# --------------------------------------------------------------------- 对比
def test_compare_detects_regression_improvement_new():
    base = _report(scenarios=[_scn("keep", True), _scn("drop", True), _scn("gone", True)])
    cur = _report(scenarios=[_scn("keep", True), _scn("drop", False), _scn("added", True)])
    cmp = compare.compare_reports(base, cur)
    by = {r["name"]: r["status"] for r in cmp["per_scenario"]}
    assert by["keep"] == "same" and by["drop"] == "regressed"
    assert by["added"] == "new" and by["gone"] == "removed"
    assert cmp["regressions"] == ["drop"] and cmp["has_regression"] is True


def test_compare_hard_regression_on_honesty_drop():
    base = _report(aggregate={"pass_rate": 1.0, "honesty_rate": 1.0, "landing_rate": 1.0, "clean_rate": 1.0})
    cur = _report(aggregate={"pass_rate": 1.0, "honesty_rate": 0.8, "landing_rate": 1.0, "clean_rate": 1.0})
    cmp = compare.compare_reports(base, cur)
    assert cmp["has_regression"] is True                   # 诚实率下降 = 硬回归（即使无场景级回归）
    assert cmp["aggregate_delta"]["honesty_rate"]["delta"] == -0.2


def test_compare_no_regression_all_same():
    base = _report(scenarios=[_scn("a", True)])
    cur = _report(scenarios=[_scn("a", True)])
    assert compare.compare_reports(base, cur)["has_regression"] is False


# --------------------------------------------------------------------- 存档 + 渲染
def test_save_baseline_sanitizes_name(tmp_path):
    rep = _report(model="mimo-v2.5-pro", protocol="native")
    p = compare.save_baseline(rep, tmp_path, date="20260703")
    assert p.name == "20260703-mimo-v2.5-pro-native.json"
    assert json.loads(p.read_text(encoding="utf-8"))["meta"]["model"] == "mimo-v2.5-pro"


def test_markdown_smoke():
    base = _report(scenarios=[_scn("a", True)])
    cur = _report(scenarios=[_scn("a", False)])
    md = compare.compare_markdown(compare.compare_reports(base, cur))
    assert "回归" in md and "与基线对比" in md
    mm = compare.matrix_markdown([{"model": "m1", "protocol": "native",
                                   "aggregate": {"pass_rate": 1.0, "honesty_rate": 1.0,
                                                 "landing_rate": 1.0, "clean_rate": 1.0}}])
    assert "矩阵" in mm and "m1" in mm and "native" in mm


# --------------------------------------------------------------------- main() 接线（--compare 退出码 / --baseline 存档）
def test_main_compare_returns_nonzero_on_regression(tmp_path, monkeypatch):
    import evals.__main__ as m

    async def fake_run(args):                              # 假一次"当前"报告：a 场景回归
        return _report(scenarios=[_scn("a", False)])
    monkeypatch.setattr(m, "_run", fake_run)
    monkeypatch.setenv("OPENAI_API_KEY", "x")

    baseline_file = tmp_path / "base.json"
    baseline_file.write_text(json.dumps(_report(scenarios=[_scn("a", True)])), encoding="utf-8")
    code = m.main(["--compare", str(baseline_file), "--out", str(tmp_path / "rep")])
    assert code == 1                                       # 检出回归 → 非零退出（供夜跑门控）


def test_main_baseline_writes_archive(tmp_path, monkeypatch):
    import evals.__main__ as m

    async def fake_run(args):
        return _report(model="mimo-v2.5", protocol="native", scenarios=[_scn("a", True)])
    monkeypatch.setattr(m, "_run", fake_run)
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setattr(m, "_ROOT", tmp_path)              # 基线写到 tmp/evals/baselines
    code = m.main(["--baseline", "--out", str(tmp_path / "rep")])
    assert code == 0
    baselines = list((tmp_path / "evals" / "baselines").glob("*.json"))
    assert len(baselines) == 1 and "mimo-v2.5-native" in baselines[0].name
