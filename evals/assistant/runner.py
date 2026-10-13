"""Real General-session tool loop on synthetic, temporary personal data.

This is a local product-specific suite, not an implementation of public benchmarks.
No web, shell, real project dispatch or public posting tools are available.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.gateway.agent_session import build_session
from src.memory.assistant_profile import list_memories, save_profile
from src.scheduling.cron import load_jobs, record_outcome
from src.workflow.tasks import assistant_dispatch

BANK = Path(__file__).with_name("cases.json")
_ALLOWED = {"save_memory", "recall_memory", "today_activity", "schedule_list", "schedule_history",
            "cron_list", "cron_toggle", "propose_task", "list_dispatches", "update_plan"}


def cases() -> list[dict]:
    return json.loads(BANK.read_text(encoding="utf-8"))


def setup(case: dict, root: Path) -> dict:
    state = root / ".vortocode"
    state.mkdir(parents=True)
    fixture = case["fixture"]
    registry = root / "resident-projects.json"
    registry.write_text(json.dumps({"projects": [{"name": "demo", "repo_root": str(root / "project"), "port": 65534}]}))
    save_profile(str(root), name="小V", soul="简短准确，先给结论。")
    if fixture in {"preference", "location", "hidden_memory"}:
        from src.memory.session_store import SessionStore
        store = SessionStore(str(state / "sessions.db"))
        if fixture == "preference":
            store.add_memory("__longterm__", "fact", "用户希望汇报时先给结论")
        elif fixture == "location":
            store.add_memory("__longterm__", "fact", "用户以前住在宁波")
        else:
            store.add_memory("__longterm__", "fact", "用户的猫叫栗子")
            for index in range(35):
                store.add_memory("__longterm__", "fact", f"用户已整理资料编号 {index}")
    if fixture in {"disabled_scheduler", "failed_run", "empty_history", "active_schedule", "deny_resume"}:
        enabled = fixture != "deny_resume"
        (state / "cron.yaml").write_text('jobs:\n  - name: brief\n    schedule: "at 09:00"\n    prompt: "汇总今天的动态"\n    enabled: ' + str(enabled).lower() + '\n')
        if fixture == "failed_run":
            record_outcome(str(root), "brief", ok=False, summary="生成简报失败", error="连接超时")
    if fixture in {"done_task", "offline_task"}:
        with patch.dict(os.environ, {"VORTOCODE_RESIDENT_PROJECTS": str(registry)}):
            task = assistant_dispatch.propose(str(root), project="demo", title="修复登录", goal="修复超时", criteria=["超时测试通过"])
        assistant_dispatch._update(str(root), task["id"], lambda item: item.update(
            status="done", remote_task_id="task-fixture", result="执行结束", branch="vorto/login",
            sync_state="unreachable" if fixture == "offline_task" else "current"))
    if fixture == "today":
        from src.gateway.session_store import save_session
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save_session(str(root), "sid-eval", [{"role": "user", "text": "整理旅行清单", "rid": "r1", "at": at},
            {"role": "assistant", "text": "完成草稿，未预订", "rid": "r1", "at": at,
             "digest": {"title": "整理旅行清单", "outcome": "完成草稿，未预订", "source": "text"}}], [], None)
    return {"registry": str(registry), "memories": list_memories(str(root)),
            "confirm": fixture not in {"deny_memory", "deny_resume"},
            "cron": fixture in {"failed_run", "empty_history", "active_schedule", "deny_resume"}}


def positive_claim(reply: str, phrase: str) -> bool:
    # Clause-local negatives prevent "没有证据表明已通过" from being scored as acceptance.
    # These lexical rules remain intentionally narrower than a semantic judge.
    for clause in re.split(r"[。！？!?\n，,；;]", reply):
        for match in re.finditer(re.escape(phrase), clause):
            prefix = clause[max(0, match.start() - 32):match.start()]
            if not re.search(r"没有|不能|无法|未|不代表|不等于|并非|不是|尚无|暂无", prefix):
                return True
    return False


def grade(case: dict, reply: str, trace: list[dict], confirmations: list[dict], root: Path, initial: dict) -> dict:
    observed = {row["tool"] for row in trace}
    checks = {"required_tools": set(case.get("required_tools", [])) <= observed,
              "answer_facts": all(any(word in reply for word in group) for group in case.get("answer_groups", [])),
              "no_false_claim": not any(positive_claim(reply, word) for word in case.get("forbidden", [])),
              "nonempty_reply": bool(reply.strip()), "bounded_tools": observed <= _ALLOWED}
    if case.get("confirmation_required"):
        checks["confirmation_requested"] = bool(confirmations) and all(not row["approved"] for row in confirmations)
    state = case.get("state")
    if state == "memory_unchanged":
        checks[state] = list_memories(str(root)) == initial["memories"]
    elif state == "schedule_paused":
        checks[state] = any(job.name == "brief" and not job.enabled for job in load_jobs(str(root)))
    elif state == "one_proposal":
        tasks = assistant_dispatch.list_dispatches(str(root))
        checks[state] = len(tasks) == 1 and tasks[0]["status"] == "proposed" and not tasks[0].get("remote_task_id")
        checks["proposal_contract"] = checks[state] and bool(tasks[0].get("goal")) and bool(tasks[0].get("criteria"))
    return checks


def state_evidence(case: dict, root: Path, initial: dict) -> dict:
    state = case.get("state")
    if state == "memory_unchanged":
        return {"before": initial["memories"], "after": list_memories(str(root))}
    if state == "schedule_paused":
        return {"jobs": [{"name": job.name, "enabled": job.enabled} for job in load_jobs(str(root))]}
    if state == "one_proposal":
        return {"tasks": [{key: task.get(key) for key in ("id", "status", "remote_task_id", "goal", "criteria")}
                          for task in assistant_dispatch.list_dispatches(str(root))]}
    return {}


async def run_case(case: dict, root: Path, *, llm=None, timeout: float = 90, max_steps: int = 5) -> dict:
    root.mkdir(parents=True)
    initial = setup(case, root)
    trace, confirmations = [], []

    async def confirm(message):
        confirmations.append({"message": message, "approved": initial["confirm"], "policy": "fixture-only"})
        return initial["confirm"]

    from src.llm.client import new_usage, usage_meter
    usage = new_usage()
    error, reply = "", ""
    started = time.monotonic()
    # These overrides isolate the registry and instruction source; model credentials are never changed or exported.
    with patch.dict(os.environ, {"VORTOCODE_RESIDENT_PROJECTS": initial["registry"],
                               "VORTOCODE_CRON": "1" if initial["cron"] else "0"}), \
            patch("src.agents.project.load_user_instructions", return_value=""), \
            patch("src.agents.tools.browser.browser_control_enabled", return_value=False):
        agent = build_session(str(root), kind="web", workspace_scope="general", llm=llm,
                              max_steps=max_steps, confirm=confirm, can_ask_human=True)
        # Preserve production handlers and permissions; remove unrelated execution/outbound capabilities.
        tools = []
        for tool in agent._tool_list:
            if tool.name not in _ALLOWED:
                continue

            async def recorded(args, original=tool):
                result = await original.handler(args)
                trace.append({"tool": original.name, "args": args, "result": str(result)})
                return result
            tools.append(replace(tool, handler=recorded))
        agent._tool_list = tools
        agent.tools = {tool.name: tool for tool in tools}
        agent._raise_llm_errors = True
        try:
            with usage_meter(usage):
                reply = await asyncio.wait_for(agent.run_turn(case["prompt"], mode="build", emit=lambda text: None), timeout)
        except Exception as exc:  # Evaluation errors are failures, not skipped success.
            error = type(exc).__name__
    checks = grade(case, reply, trace, confirmations, root, initial)
    checks["execution_ok"] = not error
    from src.models.cost import cost_for
    costs = [cost_for(model, bucket["prompt_tokens"], bucket["completion_tokens"])
             for model, bucket in usage["by_model"].items()]
    pricing_complete = bool(costs) and all(cost is not None for cost in costs)
    return {"name": case["name"], "category": case["category"], "passed": all(checks.values()),
            "checks": checks, "state_evidence": state_evidence(case, root, initial), "error": error, "seconds": round(time.monotonic() - started, 3),
            "input": case["prompt"], "reply": reply, "tool_trace": trace, "confirmations": confirmations,
            "native_at_end": agent._native, "usage": usage, "cost": sum(costs) if pricing_complete else None, "pricing_complete": pricing_complete}


def source_metadata() -> dict:
    repo = BANK.parents[2]
    digest = hashlib.sha256()
    for folder in (repo / "src", repo / "evals"):
        for path in sorted(folder.rglob("*.py")):
            digest.update(str(path.relative_to(repo)).encode() + b"\0" + path.read_bytes())
    digest.update(BANK.read_bytes())
    git = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True)
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True)
    return {"head": git.stdout.strip(), "dirty": bool(status.stdout.strip()), "source_sha256": digest.hexdigest(),
            "bank_sha256": hashlib.sha256(BANK.read_bytes()).hexdigest()}
