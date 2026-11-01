import type { FeedItem } from "./activityFeed";

/** A display projection only. It never grants approval, changes a task, or infers acceptance. */
export function assistantAttention(items: FeedItem[]) {
  const ordered = [...items].sort((a, b) => timestamp(b) - timestamp(a));
  const decisions = ordered.filter(item => item.status === "waiting");
  const exceptions = ordered.filter(item => item.status !== "waiting" && (item.requiresAttention || item.status === "failed"
    || (item.status === "stopped" && item.presentation !== "history")));
  const results = ordered.filter(item => item.status === "done" && !item.requiresAttention
    && (item.presentation === "result" || Boolean(item.taskId || item.dispatchId)));
  const recentResults = results.slice(0, 3);
  return {
    decisions, exceptions, recentResults,
    running: ordered.filter(item => item.status === "running" && !item.requiresAttention).length,
    additionalResults: results.length - recentResults.length,
  };
}

function timestamp(item: FeedItem): number {
  const value = Date.parse(item.at);
  return Number.isFinite(value) ? value : 0;
}
