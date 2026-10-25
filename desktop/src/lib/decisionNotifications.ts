// 系统通知只提醒「新增的高优先级事项」：各运行端收件箱里的 critical/high 决策，加上当前工作区的
// PR/CI 决策。id 带运行端前缀，同一事项在不同运行端不会互相吞掉。纯函数。
import type { DecisionItem, DesktopRuntimeInbox } from "../types";

const urgent = (severity: string | undefined) => severity === "critical" || severity === "high";

export function notificationCandidates(runtimeInboxes: DesktopRuntimeInbox[], decisionItems: DecisionItem[],
  currentRuntimeId: string): { id: string }[] {
  const items = runtimeInboxes.flatMap((runtimeInbox) => (runtimeInbox.snapshot?.decisions ?? [])
    .filter((item) => urgent(item.severity))
    .map((item) => ({ id: `runtime:${runtimeInbox.runtimeId}:${item.id}` })));
  decisionItems
    .filter((item) => (item.kind === "pr_check" || item.kind === "pr_review") && urgent(item.severity))
    .forEach((item) => items.push({ id: `runtime:${currentRuntimeId}:${item.id}` }));
  return items;
}

/**
 * 这一轮要提醒的事项。刚连上（还在同步）或刚打开开关时只建立基线：现有事项记为已见、不提醒。
 * 返回值里的 id 已全部写进 seen。
 */
export function takeUnseen(items: { id: string }[], seen: Set<string>, baseline: boolean): string[] {
  const unseen = baseline ? [] : items.map((item) => item.id).filter((id) => !seen.has(id));
  items.forEach((item) => seen.add(item.id));
  return unseen;
}
