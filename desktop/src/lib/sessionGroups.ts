// 侧边栏会话按「要不要人」分组：等你处理的置顶，其次进行中，其余按原顺序（最近更新在前）。
// 多个会话并行时，瓶颈是人的注意力——先看到停下来等你的那几个，而不是在时间线里一个个点开找。
import type { SessionSummary } from "../types";

export type SessionGroupKey = "needs" | "working" | "other";

export interface SessionGroup {
  key: SessionGroupKey;
  label: string;
  sessions: SessionSummary[];
}

const LABELS: Record<SessionGroupKey, string> = { needs: "等你处理", working: "进行中", other: "其他" };

/** 当前会话本地正在跑（还没等到服务端状态）也算进行中，与行内状态点同一口径。 */
export function liveSessionStatus(session: SessionSummary, active: boolean, busy: boolean): string {
  return active && busy && session.status !== "needs_input" ? "working" : session.status ?? "inactive";
}

export function sessionGroupKey(session: SessionSummary, active: boolean, busy: boolean): SessionGroupKey {
  const status = liveSessionStatus(session, active, busy);
  if (status === "needs_input" || status === "failed" || session.pending_input
    || (session.background_tasks?.attention ?? 0) > 0 || (session.hook_issues?.count ?? 0) > 0) return "needs";
  if (status === "working" || status === "queued" || (session.background_tasks?.active ?? 0) > 0) return "working";
  return "other";
}

/** 只返回非空分组；组内保持传入顺序。 */
export function groupSessions(sessions: SessionSummary[], activeSid: string, busy: boolean): SessionGroup[] {
  const buckets: Record<SessionGroupKey, SessionSummary[]> = { needs: [], working: [], other: [] };
  for (const session of sessions) buckets[sessionGroupKey(session, session.sid === activeSid, busy)].push(session);
  return (["needs", "working", "other"] as SessionGroupKey[])
    .filter((key) => buckets[key].length > 0)
    .map((key) => ({ key, label: LABELS[key], sessions: buckets[key] }));
}

/** 侧栏会话右侧的时间：今天写时分，昨天写「昨天」，今年写月日，更早带年份。同名会话靠它区分。 */
export function sessionTimeLabel(updatedSeconds: number, now: Date = new Date()): string {
  if (!updatedSeconds) return "";
  const date = new Date(updatedSeconds * 1000);
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const pad = (value: number) => String(value).padStart(2, "0");
  if (date.getTime() >= startOfToday) return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  if (date.getTime() >= startOfToday - 86_400_000) return "昨天";
  if (date.getFullYear() === now.getFullYear()) return `${date.getMonth() + 1}月${date.getDate()}日`;
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`;
}
