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
        "honest_reason": s.honest_reason, "duration_s": s.duration_s,
        "message_excerpt": s.message_excerpt,
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
    return "\n".join(lines) + "\n"
