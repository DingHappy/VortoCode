"""Explicit live-only entrypoint for the personal-assistant suite."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .runner import cases, run_case, source_metadata
from .compare import can_promote, compare_reports, evaluation_contract, latest_baseline, validate


class BoundedClient:
    """Hard request/token limits, including empty-answer nudges and final summaries."""
    def __init__(self, client, *, max_calls=6):
        self.client, self.max_calls, self.calls = client, max_calls, 0

    @property
    def config(self):
        return self.client.config

    async def chat(self, messages, **kwargs):
        if self.calls >= self.max_calls:
            raise RuntimeError("assistant evaluation request limit reached")
        self.calls += 1
        kwargs["max_tokens"] = min(int(kwargs.get("max_tokens") or 2048), 2048)
        return await self.client.chat(messages, **kwargs)


def markdown(report: dict) -> str:
    quality = report.get("quality")
    lines = ["# 私人助手能力回测", "", f"模型：{report['model']}；通过 {report['passed']}/{report['total']}。",
             "本地合成场景与真实生产工具，不是 Muse 或公开榜单成绩。", ""]
    if quality:
        lines += [f"评分：{quality['score']}/100；硬门槛失败：{', '.join(quality['hard_failures']) or '无'}。", "",
                  "| 场景 | 平均分 | 通过率 | 请求/次 | 回复字数/次 |", "|---|---|---|---|---|"]
        lines += [f"| {row['name']} | {row['score']} | {row['pass_rate']:.0%} | {row['mean_calls']:.2f} | {row['mean_reply_chars']:.1f} |"
                  for row in quality['scenarios']]
    lines += ["", "| 运行 | 通过 | 未通过检查 | 耗时 |", "|---|---|---|---|"]
    for result in report["results"]:
        failed = ", ".join(key for key, value in result["checks"].items() if not value)
        lines.append(f"| {result['name']} #{result['repeat']} | {'是' if result['passed'] else '否'} | {failed or '—'} | {result['seconds']}s |")
    comparison = report.get("comparison")
    if comparison:
        lines += ["", f"前后评分：{comparison['before_score']} → {comparison['after_score']}（{comparison['delta']:+.3f}）；结论：{comparison['verdict']}。", "",
                  "| 场景 | 前 | 后 | Δ |", "|---|---|---|---|"]
        lines += [f"| {row['name']} | {row['before']['score']} | {row['after']['score']} | {row['delta']:+.3f} |" for row in comparison['scenarios']]
        lines += ["", "回归项：" + ("；".join(comparison['regressions']) or "无"), "观察项：" + ("；".join(comparison['warnings']) or "无"),
                  "每题少于3次重复仍是小样本观察，不表示统计显著改善。"]
    if report.get("validation_error"):
        lines += ["", "报告未获比较资格：" + report["validation_error"]]
    return "\n".join(lines) + "\n"


async def run(args, chosen):
    from src.llm.client import LLMClient, LLMConfig
    config = LLMConfig(max_retries=0, timeout=30, request_timeout=75)
    client = LLMClient(config)
    results = []
    source_before = source_metadata()
    contract = evaluation_contract()
    try:
        with tempfile.TemporaryDirectory(prefix="vc-assistant-eval-") as temporary:
            for repeat in range(1, args.repeat + 1):
                for case in chosen:
                    print(f"▶ {case['name']} ({repeat}/{args.repeat})", file=sys.stderr, flush=True)
                    result = await run_case(case, Path(temporary) / f"r{repeat}" / case["name"], llm=BoundedClient(client))
                    result["repeat"] = repeat
                    results.append(result)
                    failed = [key for key, value in result["checks"].items() if not value]
                    print(f"  {'PASS' if result['passed'] else 'FAIL'} {result['seconds']}s {','.join(failed)}", file=sys.stderr, flush=True)
    finally:
        await client.close()
    source_after = source_metadata()
    source_stable = source_before["source_sha256"] == source_after["source_sha256"]
    return {"suite": "assistant-v1", "execution_mode": "live-agent-bounded-tools", "model": config.model,
            "protocol": args.protocol or ("prompt" if os.getenv("VORTOCODE_NATIVE_TOOLS") == "0" else "native"),
            "timestamp": datetime.now(timezone.utc).isoformat(), "metadata": source_after, "source_before": source_before, "source_stable": source_stable,
            "evaluation_contract": contract,
            "environment": "temporary synthetic personal data; no outbound tools; no background scheduler",
            "limits": {"steps_per_case": 5, "requests_per_case": 6, "output_tokens_per_request": 2048, "seconds_per_case": 90},
            "total": len(results), "passed": sum(result["passed"] for result in results), "results": results}


def main(args) -> int:
    bank = cases()
    names = {case["name"] for case in bank}
    if args.list:
        for case in bank:
            print(f"{case['name']:28} {case['category']:12} {case['prompt']}")
        return 0
    if not args.live and not args.candidate:
        raise ValueError("私人助手真实模型评测需显式 --live；列题用 --list。")
    if args.scenario != "all" and args.scenario not in names:
        raise ValueError(f"没有私人助手场景：{args.scenario}")
    if not 1 <= args.repeat <= 10:
        raise ValueError("repeat 必须在 1 到 10 之间")
    if args.matrix:
        raise ValueError("助手评测本轮不支持矩阵；与开发交付基线分开")
    if args.model:
        os.environ["DEFAULT_MODEL"] = args.model
    if args.protocol:
        os.environ["VORTOCODE_NATIVE_TOOLS"] = "1" if args.protocol == "native" else "0"
    chosen = bank if args.scenario == "all" else [case for case in bank if case["name"] == args.scenario]
    if args.candidate and args.live:
        raise ValueError("--candidate 是离线报告复查，不能同时 --live")
    report = (json.loads(Path(args.candidate).read_text()) if args.candidate else asyncio.run(run(args, chosen)))
    out = Path(args.out) if args.out else Path(__file__).resolve().parents[1] / "reports" / "assistant"
    out.mkdir(parents=True, exist_ok=True)
    stem = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    def save():
        (out / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / f"{stem}.md").write_text(markdown(report), encoding="utf-8")
    # Preserve raw results even when comparison or protocol validation rejects a run.
    save()
    try:
        report["quality"] = validate(report)
    except ValueError as error:
        report["validation_error"] = str(error)
        save()
        raise

    baseline_dir = Path(__file__).resolve().parents[1] / "reports" / "assistant" / "baselines"
    baseline_path = Path(args.compare) if args.compare else latest_baseline(baseline_dir, report) if args.compare_latest else None
    if args.compare_latest and baseline_path is None:
        raise ValueError("没有同模型/协议/评分合同的助手基线；先完整运行 --repeat 2 --baseline")
    comparison = None
    if baseline_path:
        try:
            comparison = compare_reports(json.loads(baseline_path.read_text()), report)
        except ValueError as error:
            report["validation_error"] = str(error)
            save()
            raise
        report["comparison"] = comparison
        report["baseline"] = str(baseline_path)

    save()
    print(f"助手评测 {report['passed']}/{report['total']}；评分 {report['quality']['score']}/100；报告：{out / (stem + '.json')}")
    if comparison:
        print(f"前后 {comparison['before_score']} → {comparison['after_score']}；{comparison['verdict']}")
    if args.baseline or args.baseline_on_green:
        if can_promote(report, comparison):
            baseline_dir.mkdir(parents=True, exist_ok=True)
            path = baseline_dir / f"{stem}.json"
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"已保存助手基线：{path}")
        else:
            print("基线未更新：需要全题集至少2次、全部通过、硬门槛通过；比较时还必须证实改善。", file=sys.stderr)
            return 1
    return 0 if report["passed"] == report["total"] and not (comparison and comparison["regressions"]) else 1
