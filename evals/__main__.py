"""评测 harness CLI：`python -m evals [--scenario all|<name>] [--model M] [--repeat N] [--out DIR]`。

真机跑分：需 .env 里的 OPENAI_API_KEY + relay base（同主程序）。默认跑全部场景各 1 遍、写报告到
evals/reports/。协议默认走生产默认（native），可 --protocol prompt 对比。
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
                s, note = await run_scenario(sc, work / f"r{rep}")
                if s is None:
                    print(f"  ⏭ {note}", file=sys.stderr, flush=True)
                else:
                    print(f"  {'✅通过' if s.passed else '❌未过'}  "
                          f"landed={s.landed} honest={s.honest} clean={s.clean}  "
                          f"[{s.honest_reason}]", file=sys.stderr, flush=True)
                outcomes.append({"name": sc.name, "run": rep, "score": s, "note": note})
    meta = {"model": os.getenv("DEFAULT_MODEL", "?"),
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
    p.add_argument("--protocol", choices=["native", "prompt"],
                   help="覆盖协议（默认跟随环境=生产默认 native）")
    p.add_argument("--out", help="报告输出目录（默认 evals/reports/）")
    p.add_argument("--list", action="store_true", help="只列出场景后退出")
    args = p.parse_args(argv)

    if args.list:
        for sc in SCENARIOS:
            print(f"  {sc.name:<20} {sc.stresses}")
        return 0

    _load_dotenv()
    if args.model:
        os.environ["DEFAULT_MODEL"] = args.model
    if args.protocol == "prompt":
        os.environ["VORTOCODE_NATIVE_TOOLS"] = "0"
    elif args.protocol == "native":
        os.environ.pop("VORTOCODE_NATIVE_TOOLS", None)
    if not os.getenv("OPENAI_API_KEY"):
        print("✗ 无 OPENAI_API_KEY（.env 缺失或未配）——真机评测需要它。", file=sys.stderr)
        return 2

    report = asyncio.run(_run(args))

    md = to_markdown(report)
    print(md)
    out_dir = Path(args.out) if args.out else _ROOT / "evals" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / f"{stamp}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    (out_dir / f"{stamp}.md").write_text(md, encoding="utf-8")
    print(f"报告已写入 {out_dir}/{stamp}.{{json,md}}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
