// 「要你处理」列表：runtime 决策队列之外，Desktop 本地再并入两类——会话里的 Hook 问题，
// 以及当前分支 PR 的失败检查与未解决 Review（旧 runtime 没有决策队列时也能看到）。纯函数。
import type { DecisionItem, PrDeliverySnapshot, SessionSummary } from "../types";

export function withLocalDecisions(decisions: DecisionItem[], sessions: SessionSummary[],
  prDelivery: PrDeliverySnapshot | null): DecisionItem[] {
  const remote: DecisionItem[] = [];
  for (const session of sessions) {
    for (const issue of session.hook_issues?.items ?? []) {
      remote.push({
        id: `hook:${session.sid}:${issue.id}`,
        kind: "hook",
        severity: "high",
        title: issue.summary || `Hook 需要处理 · ${issue.name}`,
        detail: [
          `${session.title || session.sid} · ${issue.event}${issue.tool ? ` · ${issue.tool}` : ""}`,
          issue.error || issue.message,
        ].filter(Boolean).join("\n"),
        created: issue.created || (session.updated ? new Date(session.updated * 1000).toISOString() : undefined),
        target_id: issue.id,
        session_id: session.sid,
        action: "open_session",
        can_dismiss: true,
      });
    }
  }
  if (prDelivery?.ok) {
    for (const check of prDelivery.failing_checks) {
      remote.push({
        id: `pr-check:${check.id}`,
        kind: "pr_check",
        severity: "high",
        title: `CI 失败 · ${check.name}`,
        detail: `${check.workflow ? `${check.workflow} · ` : ""}${check.conclusion || check.state || check.status || "失败"}`,
        created: check.completed_at || check.started_at,
        target_id: check.id,
        action: "open_diff",
        can_dismiss: false,
      });
    }
    prDelivery.comments.forEach((comment, index) => remote.push({
      id: `pr-review:${prDelivery.pr}:${comment.path || "general"}:${comment.line || 0}:${index}`,
      kind: "pr_review",
      severity: "high",
      title: `PR #${prDelivery.pr} 有未解决 Review`,
      detail: `${comment.path ? `${comment.path}${comment.line ? `:${comment.line}` : ""} · ` : ""}@${comment.author}: ${comment.body}`,
      target_id: String(index),
      action: "open_diff",
      can_dismiss: false,
    }));
  }
  return [...decisions, ...remote];
}
