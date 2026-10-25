import type { GatewayClient } from "../gateway";
import type { AgentTurnDetail } from "../types";

export type ActivityRecordClient = Pick<GatewayClient, "getAgentActivityDetail">;
export interface ActivityRecordSnapshot {
  record: AgentTurnDetail | null;
  loading: boolean;
  error: string;
  checkedAt: number | null;
}

export function createActivityRecordReader({ client, sid, rid, onChange, now = Date.now }: {
  client: ActivityRecordClient; sid: string; rid: string;
  onChange: (snapshot: ActivityRecordSnapshot) => void; now?: () => number;
}) {
  let disposed = false;
  let pending: Promise<boolean> | null = null;
  let snapshot: ActivityRecordSnapshot = { record: null, loading: false, error: "", checkedAt: null };
  const publish = (next: ActivityRecordSnapshot) => { snapshot = next; onChange(next); };
  const refresh = (): Promise<boolean> => {
    if (disposed) return Promise.resolve(false);
    if (pending) return pending;
    publish({ ...snapshot, loading: true });
    const task = Promise.resolve().then(() => disposed ? null : client.getAgentActivityDetail(sid, rid)).then(value => {
      if (disposed) return false;
      // A response belongs to one runtime, session and turn. Unknown event
      // statuses remain displayable, but foreign/malformed records never do.
      if (!value || value.sid !== sid.replace(/^sid-/, "") || value.rid !== rid
          || value.source !== "session_archive" || typeof value.request !== "string" || typeof value.reply !== "string"
          || typeof value.has_reply !== "boolean" || typeof value.truncated !== "boolean"
          || !Array.isArray(value.events) || value.events.some(event => !event || typeof event !== "object" || event.rid !== rid)) {
        throw new Error("Invalid activity record");
      }
      publish({ record: value, loading: false, error: "", checkedAt: now() });
      return true;
    }).catch(() => {
      if (!disposed) publish({ ...snapshot, loading: false, error: snapshot.record
        ? "本次刷新失败，以下仍是上次读取的记录；当前进度待核对。"
        : "详细过程暂不可用，当前保留的是记录摘要。你可以重新读取或打开原对话。" });
      return false;
    }).finally(() => { if (pending === task) pending = null; });
    pending = task;
    return task;
  };
  return { refresh, getSnapshot: () => snapshot,
    shouldPoll: () => !disposed && snapshot.record !== null && !snapshot.record.has_reply,
    dispose: () => { disposed = true; } };
}
