import { useEffect, useRef, useState } from "react";
import { createActivityRecordReader, type ActivityRecordClient, type ActivityRecordSnapshot } from "../lib/activityRecordReader";
import { startPolling } from "../lib/polling";

export function useActivityRecord(client: ActivityRecordClient | null | undefined, sid?: string, rid?: string) {
  const reader = useRef<ReturnType<typeof createActivityRecordReader> | null>(null);
  const [source, setSource] = useState<{ client: ActivityRecordClient; sid: string; rid: string; value: ActivityRecordSnapshot } | null>(null);
  const readable = !!(client?.getAgentActivityDetail && sid && rid);
  useEffect(() => {
    if (!client?.getAgentActivityDetail || !sid || !rid) return;
    const current = createActivityRecordReader({ client, sid, rid,
      onChange: value => setSource({ client, sid, rid, value }) });
    reader.current = current;
    void current.refresh();
    const stop = startPolling(() => current.shouldPoll() ? current.refresh() : Promise.resolve(true), {
      intervalMs: 5_000, hiddenIntervalMs: 30_000, maxBackoffMs: 30_000, visibility: document,
    });
    return () => { stop(); current.dispose(); if (reader.current === current) reader.current = null; };
  }, [client, sid, rid]);
  // Do not flash the previous runtime/turn while React runs effect cleanup.
  const snapshot: ActivityRecordSnapshot = source && source.client === client && source.sid === sid && source.rid === rid
    ? source.value : { record: null, loading: readable, error: "", checkedAt: null };
  return { ...snapshot, readable, refresh: () => { void reader.current?.refresh(); } };
}
