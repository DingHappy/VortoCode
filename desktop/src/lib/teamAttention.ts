import type { TeamSummary } from "../types";
import type { FeedItem } from "./activityFeed";

/** A list read from another connection or host is never actionable on the current host. */
export function currentTeams<T>(teams: TeamSummary[], source: { client: T; scope: string } | null, client: T | null, scope: string): TeamSummary[] {
  return client !== null && source !== null && source.client === client && source.scope === scope ? teams : [];
}

/** Explicit membership only. Missing or conflicting snapshots never hide a record. */
export function teamTaskContext(id: string | undefined, teams: TeamSummary[]): FeedItem["teamContext"] {
  if (!id) return undefined;
  const owners = teams.filter(team => team.id && team.children?.some(child => child.id === id));
  if (owners.length !== 1) return undefined;
  const team = owners[0];
  return { id: team.id, objective: team.objective, member: team.children.find(child => child.id === id)!.member };
}

export function teamRecord(item: FeedItem, teams: TeamSummary[]): FeedItem {
  const context = teamTaskContext(item.taskId, teams);
  if (!context) {
    if (!item.teamContext) return item;
    return { ...item, teamContext: undefined, sourceLabel: "后台任务",
      ...(item.status === "waiting" && !item.requiresReply ? { summary: "小组归属尚未确认，请核对", statusLabel: "归属待核对" } : {}) };
  }
  return { ...item, teamContext: context, sourceLabel: `小组成员 · ${context.member}`,
    ...(!item.requiresReply && item.status === "waiting" ? { summary: "交回小组，等待协调人审阅", statusLabel: "小组内部待审阅" } : {}) };
}
