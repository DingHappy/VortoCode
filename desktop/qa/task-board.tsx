/** Isolated UI acceptance fixture. No runtime credentials, provider or persistence. */
import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { GoalsPanel } from "../src/components/GoalsPanel";
import { useGoals } from "../src/hooks/useGoals";
import { RuntimeInboxPanel } from "../src/components/RuntimeInboxPanel";
import type { DesktopRuntimeInbox, RuntimeInboxTask } from "../src/types";
import { GatewayClient } from "../src/gateway";
import { summarizeRuntimeInboxes } from "../src/lib/runtimeInbox";
import "../src/App.css";

function source(runtimeId: string, label: string): DesktopRuntimeInbox {
  const task = (id: string, status: string, owner: string): RuntimeInboxTask => ({
    id, status, prompt: `${label} · ${id}`, detail: "查看来源任务的完整合同", owner_session: owner,
    goal_id: "goal-shared", plan_id: "", branch: "", updated: "2026-10-07T21:00:00Z",
  });
  return { runtimeId, label, scope: "project", baseUrl: "fixture-only", error: "", checkedAt: Date.now(),
    snapshot: { version: 1, scope: "project", generated_at: "2026-10-07T21:00:00Z",
      counts: { sessions_needing_input: 1, sessions_working: 1, sessions_queued: 0, decisions: 0, hook_issues: 0, goals_active: 1, goals_blocked: 0, tasks_active: 2, tasks_attention: 2 },
      sessions: ["one", "two"].map((sid, index) => ({ sid, title: `${label} 会话 ${sid}`, status: index ? "needs_input" : "working", updated: 1,
        pending_input_count: index, queue_count: 0, activity: "", running_prompt: "", cwd: "", branch: "",
        background_tasks: { active: 0, attention: 0, latest_task_id: "", active_task_id: "", attention_task_id: "" }, hook_issues: { count: 0 }, context: { pct: 0, used_tokens: 0, max_tokens: 0 } })),
      decisions: [{ id: "task-decision", kind: "task", severity: "high", title: `${label} 任务待核对事项`, detail: "定位原任务", target_id: "same-id", action: "open_task", session_id: "one", can_dismiss: false }], goals: ["draft", "active", "achieved"].map((status) => ({ id: status === "active" ? "goal-shared" : status,
        objective: `${label} ${status} 目标`, status, blocker: "", next_action: "", updated: "", progress: { passed: status === "achieved" ? 2 : 0, failed: 0, total: 2 } })),
      tasks: [task("same-id", "done", "one"), task("queue", "queued", "two"), task("run", "running", "one"), task("waiting", "waiting", "two")],
    } };
}
function Fixture() {
  const live = new URLSearchParams(location.search).get("live") === "1";
  const [sources, setSources] = useState(live ? [] : [source("a", "项目 A"), source("b", "项目 B")]);
  const refresh = async () => {
    try {
      const snapshot = await new GatewayClient({baseUrl: location.origin, token: ""}).getRuntimeInbox();
      setSources([{runtimeId: "a", label: "隔离真实台账", scope: "project", baseUrl: location.origin, error: "", checkedAt: Date.now(), snapshot}]);
    } catch (error) { setReceipt(String(error)); }
  };
  useEffect(() => { if (live) void refresh(); }, [live]);
  const summary = summarizeRuntimeInboxes(sources.map((entry) => ({counts: entry.snapshot?.counts, error: entry.error, firstSeenAt: entry.checkedAt, everReachable: !!entry.snapshot})), Date.now());
  const [receipt, setReceipt] = useState("");
  const clientRef = useRef<GatewayClient | null>(null);
  if (!clientRef.current) clientRef.current = new GatewayClient({baseUrl: location.origin, token: ""});
  const [showGoals, setShowGoals] = useState(false);
  const goalState = useGoals(clientRef, setReceipt, () => setShowGoals(true));
  return <main style={{ maxWidth: 1300, margin: "0 auto", minWidth: 0 }}>
    <header style={{ padding: 12 }}><strong>{live ? "原 Desktop / 原服务接口 · 独立临时台账 · 无模型" : "隔离验收 fixture · 原 Desktop 组件 · 无模型、无持久化"}</strong>
      <button onClick={() => setSources((old) => old.map((entry) => entry.runtimeId === "b" ? { ...entry, error: entry.error ? "" : "offline" } : entry))}>切换 B 失联</button>
      <button onClick={() => setSources((old) => old.filter((entry) => entry.runtimeId !== "b"))}>移除 B 来源</button>
      {live && <button onClick={async () => {
        const pending = sources[0]?.snapshot?.tasks.find((entry) => entry.id === "task-parent");
        if (pending?.lifecycle?.revision) {
          await new GatewayClient({baseUrl: location.origin, token: ""}).acknowledgeTaskHandoff(pending.id, "owner", pending.lifecycle.revision, "隔离验收：核对当前结果并处理交接");
          await refresh();
        }
      }}>隔离确认 parent 交接</button>}
      {live && <button onClick={async () => { await globalThis.fetch("/qa/change-parent", {method: "POST"}); await refresh(); }}>隔离修改前置结果</button>}
      {live && <button onClick={() => {setShowGoals(true); void goalState.focusGoal("goal-missing");}}>隔离定位缺失目标</button>}
      {showGoals && <button onClick={() => setShowGoals(false)}>返回隔离看板</button>}
      <output id="navigation-receipt">{receipt}</output></header>
    {!showGoals && <RuntimeInboxPanel runtimeInboxes={sources} summary={summary}
      currentRuntimeId="a" onRefresh={() => { if (live) void refresh(); else setReceipt("refresh"); }}
      onActivate={(entry) => setReceipt(JSON.stringify([entry.runtimeId, "activate"]))}
      onOpenSession={(entry, sid, needsInput) => setReceipt(JSON.stringify([entry.runtimeId, "session", sid, needsInput]))}
      onOpenPanel={(entry, tab, sid, taskId, goalId) => {
        setReceipt(JSON.stringify([entry.runtimeId, tab, sid || "", taskId || "", goalId || ""]));
        if (live && tab === "goals") {setShowGoals(true); goalState.resetGoalNavigation(); if (goalId) void goalState.focusGoal(goalId); else void goalState.refreshGoals();}
      }} />}
    {showGoals && <GoalsPanel goals={goalState.goals} goalFocus={goalState.goalFocus}
      onClearFocus={() => {goalState.resetGoalNavigation(); void goalState.refreshGoals();}}
      onRetryFocus={() => {if (goalState.goalFocus) void goalState.focusGoal(goalState.goalFocus.id);}}
      tasks={[]} runs={[]} connection="connected" editingGoalId={null}
      goalObjective="" goalCriteria="" goalConstraints="" goalNonGoals="" goalSubmitting={false}
      goalEvidenceDrafts={{}} goalVerifierDrafts={{}}
      onObjectiveChange={() => {}} onCriteriaChange={() => {}} onConstraintsChange={() => {}} onNonGoalsChange={() => {}}
      onEvidenceDraftsChange={() => {}} onVerifierDraftsChange={() => {}} onResetForm={() => {}}
      onSaveDraft={() => {}} onEditDraft={() => {}} onDeleteDraft={() => {}} onRunGoal={() => {}}
      onRunVerifiers={() => {}} onSaveVerifier={() => {}} onRecordEvidence={() => {}} /> }
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
