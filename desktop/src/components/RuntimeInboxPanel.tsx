// App.tsx 抽出的「收件箱」面板（纯展示件，只搬家不改行为）。
// 汇总所有本地 runtime 的待处理/执行中工作；数据全是 App 传入的只读快照，
// 切换 runtime、打开会话或面板等副作用都经回调交还 App。
import { CircleAlert, CircleCheck, Inbox } from "lucide-react";

import type { InspectorTab } from "../lib/inspector";
import { formatRelativeTime, statusLabel } from "../lib/labels";
import type { InboxSummary } from "../lib/runtimeInbox";
import type { DesktopRuntimeInbox } from "../types";

type InboxPanelTab = Extract<InspectorTab, "decisions" | "goals" | "tasks" | "runs">;

type RuntimeInboxPanelProps = {
  runtimeInboxes: DesktopRuntimeInbox[];
  summary: InboxSummary;
  currentRuntimeId: string | undefined;
  onRefresh: () => void;
  onActivate: (runtimeInbox: DesktopRuntimeInbox) => void;
  onOpenSession: (runtimeInbox: DesktopRuntimeInbox, sid: string, needsInput: boolean) => void;
  onOpenPanel: (runtimeInbox: DesktopRuntimeInbox, tab: InboxPanelTab, sid?: string, taskId?: string) => void;
};

export function RuntimeInboxPanel({
  runtimeInboxes,
  summary,
  currentRuntimeId,
  onRefresh,
  onActivate,
  onOpenSession,
  onOpenPanel,
}: RuntimeInboxPanelProps) {
  return (
    <div className="runtime-inbox-panel">
      <div className="runtime-inbox-hero">
        <div>
          <span>DESKTOP CONTROL PLANE</span>
          <h2>所有工作，一处处理</h2>
          <p>后台项目继续运行；这里只汇总需要你关注、正在执行和可以继续的工作。</p>
        </div>
        <button onClick={onRefresh} title="立即刷新所有本地 runtime">刷新</button>
      </div>
      <div className="runtime-inbox-metrics">
        <div className={summary.actionable > 0 ? "attention" : ""}><strong>{summary.actionable}</strong><span>需要处理</span></div>
        <div><strong>{summary.working}</strong><span>Agent 执行中</span></div>
        <div><strong>{summary.tasks}</strong><span>后台工作</span></div>
        <div><strong>{summary.goals}</strong><span>活跃目标</span></div>
      </div>

      <div className="runtime-inbox-list">
        {runtimeInboxes.length === 0 && (
          <div className="panel-empty">
            <Inbox size={22} />
            <strong>还没有可汇总的本地工作</strong>
            <span>打开通用会话或项目后，Desktop 会自动在这里发现它。</span>
          </div>
        )}
        {[...runtimeInboxes].sort((left, right) => {
          const leftCounts = left.snapshot?.counts;
          const rightCounts = right.snapshot?.counts;
          const leftScore = (leftCounts?.decisions ?? 0) + (leftCounts?.hook_issues ?? 0)
            + (leftCounts?.goals_blocked ?? 0) + (leftCounts?.tasks_attention ?? 0);
          const rightScore = (rightCounts?.decisions ?? 0) + (rightCounts?.hook_issues ?? 0)
            + (rightCounts?.goals_blocked ?? 0) + (rightCounts?.tasks_attention ?? 0);
          return rightScore - leftScore || left.label.localeCompare(right.label, "zh-CN");
        }).map((runtimeInbox) => {
          const snapshot = runtimeInbox.snapshot;
          const counts = snapshot?.counts;
          const actionable = (counts?.decisions ?? 0) + (counts?.hook_issues ?? 0)
            + (counts?.goals_blocked ?? 0) + (counts?.tasks_attention ?? 0);
          const importantSessions = (snapshot?.sessions ?? [])
            .filter((session) => ["needs_input", "failed", "working", "queued"].includes(session.status))
            .slice(0, 5);
          const importantGoals = (snapshot?.goals ?? [])
            .filter((goal) => goal.status !== "achieved")
            .slice(0, 4);
          const importantTasks = (snapshot?.tasks ?? [])
            .filter((task) => ["queued", "running", "cancelling", "blocked", "failed", "paused", "interrupted"].includes(task.status))
            .slice(0, 4);
          return (
            <section className={`runtime-inbox-card ${runtimeInbox.runtimeId === currentRuntimeId ? "current" : ""} ${runtimeInbox.error ? "degraded" : ""}`} key={runtimeInbox.runtimeId}>
              <button className="runtime-inbox-card-head" onClick={() => onActivate(runtimeInbox)}>
                <span className={`runtime-inbox-runtime-dot ${runtimeInbox.error ? "error" : actionable > 0 ? "attention" : (counts?.sessions_working ?? 0) > 0 ? "working" : "healthy"}`} />
                <span className="runtime-inbox-title">
                  <strong>{runtimeInbox.label}</strong>
                  <small>{runtimeInbox.scope === "general" ? "通用" : runtimeInbox.scope === "scratch" ? "Scratch" : runtimeInbox.repoRoot || "项目"}</small>
                </span>
                <span className="runtime-inbox-card-meta">
                  {actionable > 0 && <b>{actionable} 待处理</b>}
                  {(counts?.sessions_working ?? 0) > 0 && <em>{counts?.sessions_working} 执行中</em>}
                  <small>{runtimeInbox.checkedAt ? formatRelativeTime(runtimeInbox.checkedAt / 1000) : ""}</small>
                </span>
              </button>

              {runtimeInbox.error && (
                <div className="runtime-inbox-error">
                  <CircleAlert size={14} />
                  <span><strong>暂时无法刷新</strong><small>{runtimeInbox.error}</small></span>
                </div>
              )}

              {(snapshot?.decisions ?? []).slice(0, 5).map((decision) => {
                const targetTab = decision.kind === "goal" ? "goals"
                  : decision.kind === "task" ? "tasks"
                    : decision.kind === "run" ? "runs" : "decisions";
                return (
                  <button className="runtime-inbox-item decision" key={`decision:${decision.id}`} onClick={() => onOpenPanel(runtimeInbox, targetTab, decision.session_id)}>
                    <span className={`runtime-inbox-item-dot severity-${decision.severity}`} />
                    <span><strong>{decision.title}</strong><small>{decision.detail || "等待你的处理"}</small></span>
                    <em>处理</em>
                  </button>
                );
              })}

              {importantSessions.map((session) => (
                <button className="runtime-inbox-item" key={`session:${session.sid}`} onClick={() => onOpenSession(runtimeInbox, session.sid, session.status === "needs_input" || session.status === "failed")}>
                  <span className={`runtime-inbox-item-dot status-${session.status}`} />
                  <span>
                    <strong>{session.title || "新任务"}</strong>
                    <small>{session.status === "needs_input"
                      ? `${session.pending_input_count || 1} 项等待确认`
                      : session.status === "failed" ? "任务需要处理"
                        : session.activity || session.running_prompt || statusLabel(session.status)}</small>
                  </span>
                  <em>{session.branch || statusLabel(session.status)}</em>
                </button>
              ))}

              {importantGoals.map((goal) => (
                <button className="runtime-inbox-item" key={`goal:${goal.id}`} onClick={() => onOpenPanel(runtimeInbox, "goals")}>
                  <span className={`runtime-inbox-item-dot status-${goal.status}`} />
                  <span><strong>{goal.objective || "未命名目标"}</strong><small>{goal.blocker || goal.next_action || `${goal.progress.passed}/${goal.progress.total} 条完成标准`}</small></span>
                  <em>{statusLabel(goal.status)}</em>
                </button>
              ))}

              {importantTasks.map((task) => (
                <button className="runtime-inbox-item" key={`task:${task.id}`} onClick={() => onOpenPanel(runtimeInbox, "tasks", task.owner_session, task.id)}>
                  <span className={`runtime-inbox-item-dot status-${task.status}`} />
                  <span><strong>{task.prompt || "后台工作"}</strong><small>{task.detail || task.branch || task.id}</small></span>
                  <em>{statusLabel(task.status)}</em>
                </button>
              ))}

              {snapshot && actionable === 0 && importantSessions.length === 0 && importantGoals.length === 0 && importantTasks.length === 0 && (
                <div className="runtime-inbox-clear"><CircleCheck size={15} />当前没有需要关注的工作</div>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}
