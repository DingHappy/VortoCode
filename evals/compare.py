"""B3 · 评测基线存档 + 回归对比 + 模型/协议矩阵——把"1/3 vs 3/3 轶事"换成有统计基线的对比。

纯函数（不触模型/不落 LLM），可 CI 单测：
  - save_baseline：把一次报告存到 evals/baselines/<date>-<model>-<protocol>.json
  - compare_reports：逐场景对比通过率 Δ，标出回归/进步/新增（回归 → 调用方给非零 exit）
  - parse_matrix：解析 "m1,m2 × native,prompt" → [(model, protocol), …] 全组合
  - matrix_markdown：把多组结果汇成一张对比表
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# --------------------------------------------------------------------- 存档
def save_baseline(report: dict, baseline_dir: Path, *, date: str) -> Path:
    """把报告存成基线：<date>-<model>-<protocol>.json。返回路径。date 由调用方给（避免这里读时钟）。"""
    meta = report.get("meta", {})
    model = re.sub(r"[^A-Za-z0-9._-]", "_", str(meta.get("model", "unknown")))
    proto = re.sub(r"[^A-Za-z0-9._-]", "_", str(meta.get("protocol", "unknown")))
    baseline_dir.mkdir(parents=True, exist_ok=True)
    p = baseline_dir / f"{date}-{model}-{proto}.json"
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def latest_baseline(baseline_dir: Path, *, model: Optional[str] = None,
                    protocol: Optional[str] = None) -> Optional[Path]:
    """最新的基线文件（按文件名里的日期前缀排，同日取字典序末位）。没有则 None。

    夜跑要"和上一版基线比"，但基线文件名带日期/模型/协议——让 LLM 去 ls 目录挑一个既费 token
    又可能挑错。这里做成确定性解析：可按 model/protocol 过滤，只比同型号同协议的（跨模型比没意义）。
    """
    if not baseline_dir.is_dir():
        return None
    cands = []
    for p in baseline_dir.glob("*.json"):
        # 文件名是 save_baseline 造的 <date>-<model>-<protocol>；按**字段**精确匹配，
        # 不能用子串——`mimo-v2.5` 会匹到 `mimo-v2.5-pro`，那就成了拿 pro 的基线比普通型号。
        date, sep, rest = p.stem.partition("-")
        if not sep or not date.isdigit():
            continue
        f_model, sep2, f_proto = rest.rpartition("-")   # 模型名自身可含 '-'，协议在最后一段
        if not sep2:
            continue
        if model and f_model != re.sub(r"[^A-Za-z0-9._-]", "_", model):
            continue
        if protocol and f_proto != protocol:
            continue
        cands.append(p)
    return max(cands, key=lambda p: p.stem) if cands else None


def load_report(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------- 对比
def pass_rate_by_scenario(report: dict) -> Dict[str, float]:
    """每个场景的通过率（同名多次运行取均值）。"""
    buckets: Dict[str, List[bool]] = {}
    for row in report.get("scenarios", []):
        buckets.setdefault(row["name"], []).append(bool(row.get("passed")))
    return {name: sum(v) / len(v) for name, v in buckets.items() if v}


def compare_reports(baseline: dict, current: dict) -> dict:
    """逐场景 + 聚合对比 current vs baseline。返回 {per_scenario, aggregate_delta, regressions, has_regression}。

    回归 = 某场景通过率下降，或聚合诚实率/落地率下降。诚实率下降视为**硬回归**（诚实性是护城河底线）。
    """
    b_scn, c_scn = pass_rate_by_scenario(baseline), pass_rate_by_scenario(current)
    per_scenario = []
    regressions = []
    for name in sorted(set(b_scn) | set(c_scn)):
        b = b_scn.get(name)
        c = c_scn.get(name)
        if b is None:
            status = "new"
        elif c is None:
            status = "removed"
        elif c < b - 1e-9:
            status = "regressed"
            regressions.append(name)
        elif c > b + 1e-9:
            status = "improved"
        else:
            status = "same"
        per_scenario.append({"name": name, "baseline": b, "current": c,
                             "delta": (None if (b is None or c is None) else round(c - b, 4)),
                             "status": status})

    b_agg, c_agg = baseline.get("aggregate", {}), current.get("aggregate", {})
    agg_delta = {}
    hard_regression = False
    for key in ("pass_rate", "honesty_rate", "landing_rate", "clean_rate"):
        b, c = b_agg.get(key), c_agg.get(key)
        agg_delta[key] = {"baseline": b, "current": c,
                          "delta": (None if (b is None or c is None) else round(c - b, 4))}
        if b is not None and c is not None and c < b - 1e-9 and key in ("honesty_rate", "landing_rate"):
            hard_regression = True                       # 诚实/落地下降 = 硬回归

    return {"per_scenario": per_scenario, "aggregate_delta": agg_delta,
            "regressions": regressions, "has_regression": bool(regressions) or hard_regression}


def compare_markdown(cmp: dict) -> str:
    lines = ["## 与基线对比", "", "| 场景 | 基线 | 当前 | Δ | 状态 |", "| --- | --- | --- | --- | --- |"]
    mark = {"regressed": "🔴 回归", "improved": "🟢 进步", "new": "🆕 新增",
            "removed": "⚪ 移除", "same": "· 持平"}
    for r in cmp["per_scenario"]:
        b = "—" if r["baseline"] is None else f"{r['baseline'] * 100:.0f}%"
        c = "—" if r["current"] is None else f"{r['current'] * 100:.0f}%"
        d = "—" if r["delta"] is None else f"{r['delta'] * 100:+.0f}%"
        lines.append(f"| {r['name']} | {b} | {c} | {d} | {mark.get(r['status'], r['status'])} |")
    def _agg_cell(v: dict) -> str:
        return "—" if v["delta"] is None else f"{v['delta'] * 100:+.0f}%"
    agg = cmp["aggregate_delta"]
    lines += ["", "**聚合：** " + "　".join(f"{k} {_agg_cell(v)}" for k, v in agg.items())]
    if cmp["has_regression"]:
        lines += ["", f"⚠️ **检出回归**：{', '.join(cmp['regressions']) or '（聚合诚实/落地下降）'}"]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------- 矩阵
def parse_matrix(spec: str) -> List[Tuple[str, Optional[str]]]:
    """'m1,m2 × native,prompt' → [(m1,native),(m1,prompt),(m2,native),(m2,prompt)]。

    无 × 分隔（如 'm1,m2'）→ 只跑这些模型、协议用默认（None）。× 也接受小写 x（两侧留空格 ' x '）。
    """
    s = (spec or "").strip()
    if not s:
        return []
    parts = re.split(r"\s*[×]\s*|\s+x\s+", s, maxsplit=1)
    models = [m.strip() for m in parts[0].split(",") if m.strip()]
    if len(parts) == 2:
        protocols: List[Optional[str]] = [p.strip() for p in parts[1].split(",") if p.strip()]
    else:
        protocols = [None]
    return [(m, p) for m in models for p in protocols]


def matrix_markdown(rows: List[dict]) -> str:
    """rows: [{model, protocol, aggregate:{pass_rate,honesty_rate,landing_rate,clean_rate}}]。"""
    def pct(x):
        return "—" if x is None else f"{x * 100:.0f}%"
    out = ["## 模型 × 协议矩阵", "",
           "| 模型 | 协议 | 通过率 | 诚实率 | 落地率 | 干净率 |",
           "| --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        a = r.get("aggregate", {})
        out.append(f"| {r['model']} | {r.get('protocol') or '默认'} | {pct(a.get('pass_rate'))} | "
                   f"{pct(a.get('honesty_rate'))} | {pct(a.get('landing_rate'))} | {pct(a.get('clean_rate'))} |")
    return "\n".join(out) + "\n"
