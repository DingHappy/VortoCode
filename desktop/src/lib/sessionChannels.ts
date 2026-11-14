import type { SessionSummary } from "../types";

/** Only explicit versioned metadata denotes a channel. Old titles and SIDs stay ordinary chats. */
export function sessionChannel(session: Pick<SessionSummary, "channel"> | undefined) {
  const value: unknown = session?.channel;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  return Object.keys(row).length === 2 && row.version === 1 && typeof row.topic === "string" && row.topic.length <= 300
    ? value as NonNullable<SessionSummary["channel"]> : null;
}

export function createdChannel(value: unknown): SessionSummary {
  const row = value && typeof value === "object" && "session" in value ? value.session : null;
  if (row && typeof row === "object" && "sid" in row && typeof row.sid === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(row.sid)
    && "title" in row && typeof row.title === "string" && row.title.trim().length > 0 && row.title.length <= 30
    && "messages" in row && row.messages === 0 && "updated" in row && typeof row.updated === "number" && Number.isFinite(row.updated)
    && sessionChannel(row as SessionSummary)) return row as SessionSummary;
  throw new Error("频道保存回执不完整，请刷新原空间核对；未自动重试");
}
