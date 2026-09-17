"""把评测结果渲染成 JSON（机读/存档）+ markdown（人看/贴 README 基线）。"""

from __future__ import annotations

from typing import List, Optional

from .scoring import Score, aggregate


def build_report(outcomes: List[dict], meta: dict) -> dict:
    """outcomes: [{name, run, score: Score|None, note: str}]。None = 跳过（note 说明原因）。"""
    scores = [o["score"] for o in outcomes if o["score"] is not None]
    skipped = [{"name": o["name"], "note": o["note"]} for o in outcomes if o["score"] is None]
    return {
        "meta": meta,
        "aggregate": aggregate(scores),
        "scenarios": [_score_row(o) for o in outcomes if o["score"] is not None],
        "skipped": skipped,
    }


def _score_row(o: dict) -> dict:
    s: Score = o["score"]
    return {
        "name": s.name, "run": o.get("run", 1),
        "landed": s.landed, "honest": s.honest, "clean": s.clean,
        "passed": s.passed, "expect_land": s.expect_land, "land_ok": s.land_ok,
        "surfaced": s.surfaced, "changed_ok": s.changed_ok,
        "honest_reason": s.honest_reason, "duration_s": s.duration_s,
        "message_excerpt": s.message_excerpt,
        "evidence": s.evidence,
        "execution_error": s.execution_error,
    }


def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _b(v: bool) -> str:
    return "✅" if v else "❌"


def to_markdown(report: dict) -> str:
    m, agg = report["meta"], report["aggregate"]
    lines = [
        f"# 评测报告 · {m.get('model', '?')} · {m.get('timestamp', '')}",
        "",
        f"- 模型：`{m.get('model', '?')}`　协议：`{m.get('protocol', '?')}`　"
        f"仓库 HEAD：`{m.get('head', '?')}`　重复：{m.get('repeat', 1)}",
        f"- 执行层：`{m.get('execution_mode', 'tool')}`　工作区有未提交改动：{m.get('dirty', '未记录')}",
        f"- **落地率** {_pct(agg['landing_rate'])}（{agg['landing_n']} 个期望落地的场景）　"
        f"**诚实率** {_pct(agg['honesty_rate'])}　**干净率** {_pct(agg['clean_rate'])}　"
        f"**总通过率** {_pct(agg['pass_rate'])}（{agg['n']} 次运行）",
        "",
        "| 场景 | run | 落地 | 诚实 | 干净 | 通过 | 诚实性判定 | 耗时 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in report["scenarios"]:
        land = _b(r["landed"]) + ("" if r["expect_land"] else "（不要求）")
        lines.append(f"| {r['name']} | {r['run']} | {land} | {_b(r['honest'])} | "
                     f"{_b(r['clean'])} | {_b(r['passed'])} | {r['honest_reason']} | {r['duration_s']}s |")
    if report["skipped"]:
        lines += ["", "**跳过：**"] + [f"- {s['name']}：{s['note']}" for s in report["skipped"]]
    evidence_rows = [r for r in report["scenarios"] if r.get("evidence")]
    if evidence_rows:
        lines += ["", "## 用量与独立验收", "",
                  "| 场景 | tokens | 估算费用 | 独立业务断言 | 交付 commit |",
                  "| --- | --- | --- | --- | --- |"]
        for row in evidence_rows:
            e = row["evidence"]
            cost = "未知" if e["cost"] is None else f"${e['cost']:.4f}"
            accepted = e["independent_acceptance"]["ok"]
            mark = "未配置" if accepted is None else _b(accepted)
            lines.append(f"| {row['name']} | {e['usage']['total_tokens']} | {cost} | {mark} | "
                         f"{e['delivered_commit'][:12] or '无'} |")
        lines += ["", "完整输入、逐模型用量、工具轨迹、自动确认记录与验收输出见同名 JSON。"
                  "费用按配置目录估算，未知定价不记为免费；这些任务运行于临时仓库。"]
    return "\n".join(lines) + "\n"
