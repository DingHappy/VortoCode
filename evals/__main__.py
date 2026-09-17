"""评测 harness CLI：`python -m evals [--scenario all|<name>] [--model M] [--repeat N] [--out DIR]`。

真机跑分：需 .env 里的 OPENAI_API_KEY + relay base（同主程序）。默认跑全部场景各 1 遍、写报告到
evals/reports/。协议默认走生产默认（native），可 --protocol prompt 对比。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import hashlib
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .report import build_report, to_markdown
from .runner import run_scenario
from .scenarios import BY_NAME, SCENARIOS

_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """best-effort 载入仓库根 .env（与主程序一致；缺 python-dotenv 就手动解析）。"""
    env = _ROOT / ".env"
    if not env.is_file():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env)
        return
    except Exception:  # noqa: BLE001
        pass
    for ln in env.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _head() -> str:
    try:
        return subprocess.run(["git", "-C", str(_ROOT), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip() or "?"
    except Exception:  # noqa: BLE001
        return "?"


async def _run(args) -> dict:
    chosen = SCENARIOS if args.scenario == "all" else [BY_NAME[args.scenario]]
    outcomes = []
    import tempfile
    with tempfile.TemporaryDirectory(prefix="vc-eval-") as td:
        work = Path(td)
        for rep in range(1, args.repeat + 1):
            for sc in chosen:
                label = f"{sc.name} (run {rep}/{args.repeat})"
                print(f"▶ {label} …", file=sys.stderr, flush=True)
                s, note = await run_scenario(sc, work / f"r{rep}", via_agent=args.via_agent)
                if s is None:
                    print(f"  ⏭ {note}", file=sys.stderr, flush=True)
                else:
                    print(f"  {'✅通过' if s.passed else '❌未过'}  "
                          f"landed={s.landed} honest={s.honest} clean={s.clean}  "
                          f"[{s.honest_reason}]", file=sys.stderr, flush=True)
                outcomes.append({"name": sc.name, "run": rep, "score": s, "note": note})
    source_hash = hashlib.sha256()
    for directory in ("src", "evals"):
        for path in sorted((_ROOT / directory).rglob("*.py")):
            source_hash.update(str(path.relative_to(_ROOT)).encode() + b"\0" + path.read_bytes())
    status = subprocess.run(["git", "-C", str(_ROOT), "status", "--porcelain"],
                            capture_output=True, text=True)
    meta = {"model": os.getenv("DEFAULT_MODEL", "?"),
            "execution_mode": "agent" if args.via_agent else "tool",
            "harness_source_sha256": source_hash.hexdigest(),
            "dirty": bool(status.stdout.strip()) if status.returncode == 0 else None,
            "protocol": "prompt" if os.getenv("VORTOCODE_NATIVE_TOOLS") in ("0", "false", "no", "off")
                        else "native",
            "head": _head(), "repeat": args.repeat,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")}
    return build_report(outcomes, meta)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m evals", description="VortoCode 真机评测 harness")
    p.add_argument("--scenario", default="all",
                   help="场景名或 all（默认 all）。可选：" + ", ".join(BY_NAME))
    p.add_argument("--model", help="覆盖 DEFAULT_MODEL（如 mimo-v2.5 / mimo-v2.5-pro）")
    p.add_argument("--repeat", type=int, default=1, help="每个场景重复次数（算通过率）")
    p.add_argument("--via-agent", action="store_true", help="通过真实 MainAgent 对话与工具选择执行任务")
    p.add_argument("--protocol", choices=["native", "prompt"],
                   help="覆盖协议（默认跟随环境=生产默认 native）")
    p.add_argument("--out", help="报告输出目录（默认 evals/reports/）")
    p.add_argument("--list", action="store_true", help="只列出场景后退出")
    p.add_argument("--baseline", action="store_true",
                   help="把本次报告存成基线到 evals/baselines/<date>-<model>-<protocol>.json")
    p.add_argument("--compare", metavar="FILE",
                   help="与一个基线报告对比，逐场景 Δ；检出回归则退出码非零")
    p.add_argument("--compare-latest", action="store_true",
                   help="与 evals/baselines/ 里**最新的同模型同协议**基线对比（夜跑用；无基线则跳过对比）")
    p.add_argument("--baseline-on-green", action="store_true",
                   help="没有回归时把本次报告存成新基线（夜跑用：绿了就把水位线抬上去）")
    p.add_argument("--matrix", metavar="SPEC",
                   help="模型×协议矩阵串行跑，汇一张对比表（如 'mimo-v2.5,mimo-v2.5-pro × native,prompt'）")
    args = p.parse_args(argv)

    if args.scenario != "all" and args.scenario not in BY_NAME:
        p.error(f"未知场景：{args.scenario}")
    if args.repeat < 1:
        p.error("--repeat 必须至少为 1")

    if args.list:
        for sc in SCENARIOS:
            print(f"  {sc.name:<20} {sc.stresses}")
        return 0

    _load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        print("✗ 无 OPENAI_API_KEY（.env 缺失或未配）——真机评测需要它。", file=sys.stderr)
        return 2

    out_dir = Path(args.out) if args.out else _ROOT / "evals" / "reports"

    # 矩阵模式：串行跑每个 (model, protocol) 组合，汇总对比表
    if args.matrix:
        from .compare import matrix_markdown, parse_matrix
        combos = parse_matrix(args.matrix)
        if not combos:
            print(f"✗ 无法解析 --matrix：{args.matrix}", file=sys.stderr)
            return 2
        rows = []
        matrix_ok = True
        for model, proto in combos:
            _apply_model_protocol(model, proto)
            print(f"▶▶ 矩阵：model={model} protocol={proto or '默认'}", file=sys.stderr, flush=True)
            rep = asyncio.run(_run(args))
            matrix_ok = matrix_ok and _report_passed(rep)
            rows.append({"model": model, "protocol": proto, "aggregate": rep["aggregate"]})
        md = matrix_markdown(rows)
        print(md)
        _write_report({"meta": {"matrix": args.matrix}, "rows": rows}, out_dir, md=md, kind="matrix")
        return 0 if matrix_ok else 1

    _apply_model_protocol(args.model, args.protocol)
    report = asyncio.run(_run(args))
    md = to_markdown(report)

    # 与基线对比（检出回归 → 非零退出码，供 CI/夜跑门控）
    from .compare import (compare_markdown, compare_reports, latest_baseline, load_report,
                          save_baseline)
    baselines_dir = _ROOT / "evals" / "baselines"
    if args.via_agent:
        baselines_dir /= "agent"
    exit_code = 0 if _report_passed(report) else 1
    compared = False
    baseline_path = args.compare
    if not baseline_path and args.compare_latest:
        # 夜跑：确定性地挑"最新的同模型同协议基线"（跨模型比没意义）；没有基线就跳过对比、不算红
        meta = report.get("meta", {})
        found = latest_baseline(baselines_dir, model=meta.get("model"),
                                protocol=meta.get("protocol"))
        if found is None:
            print(f"（{baselines_dir} 里没有同模型同协议的基线，跳过对比；"
                  f"可用 --baseline 先存一条水位线）", file=sys.stderr)
        else:
            baseline_path = str(found)
            print(f"对比基线：{found.name}", file=sys.stderr)
    if baseline_path:
        cmp = compare_reports(load_report(baseline_path), report)
        md += "\n" + compare_markdown(cmp)
        compared = True
        if cmp["has_regression"]:
            exit_code = 1

    print(md)
    _write_report(report, out_dir, md=md, kind="report")

    # 绿了就把水位线抬上去（夜跑：无回归 → 新基线；有回归 → 保留旧基线，别把退化固化成新标准）
    if args.baseline or (args.baseline_on_green and exit_code == 0
                         and (compared or not args.compare_latest)):
        path = save_baseline(report, baselines_dir, date=datetime.now().strftime("%Y%m%d"))
        print(f"基线已存档：{path}", file=sys.stderr)
    return exit_code


def _report_passed(report: dict) -> bool:
    rows = report.get("scenarios") or []
    return bool(rows) and not report.get("skipped") and all(r.get("passed") is True for r in rows)


def _apply_model_protocol(model, protocol) -> None:
    """把 model/protocol 落到环境（DEFAULT_MODEL / VORTOCODE_NATIVE_TOOLS）。"""
    if model:
        os.environ["DEFAULT_MODEL"] = model
    if protocol == "prompt":
        os.environ["VORTOCODE_NATIVE_TOOLS"] = "0"
    elif protocol == "native":
        os.environ.pop("VORTOCODE_NATIVE_TOOLS", None)


def _write_report(report: dict, out_dir: Path, *, md: str, kind: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / f"{stamp}-{kind}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
    (out_dir / f"{stamp}-{kind}.md").write_text(md, encoding="utf-8")
    print(f"报告已写入 {out_dir}/{stamp}-{kind}.{{json,md}}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
