import { freshAt } from "./readFreshness";

export type ActivitySourceName = "turns" | "dispatches" | "codexActions" | "notices" | "tasks";
export type ActivityRead = { status: "loading" | "current" | "unavailable"; checkedAt?: number; checking?: boolean };
export type ActivitySource = ActivityRead & { name: ActivitySourceName };
export const ACTIVITY_SOURCE_LABEL: Record<ActivitySourceName, string> = {
  turns: "对话动态", dispatches: "助理任务单", codexActions: "Codex 动作", notices: "简报与通知", tasks: "项目任务",
};

/** Counts only describe received records; incomplete or expired reads cannot prove zero pending work. */
export function activityCoverage(sources: ActivitySource[], connected: boolean, now: number) {
  const issues = sources.filter(source => !connected || source.status !== "current" || !freshAt(source.checkedAt, now));
  const complete = connected && sources.length > 0 && issues.length === 0;
  const checkedAt = complete ? Math.min(...sources.map(source => source.checkedAt!)) : undefined;
  return { complete, issues, checkedAt };
}
