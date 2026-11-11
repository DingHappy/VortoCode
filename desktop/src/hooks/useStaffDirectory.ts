import { useEffect, useState, type RefObject } from "react";
import type { GatewayClient } from "../gateway";
import type { StaffSnapshot } from "../lib/staff";
import { startPolling } from "../lib/polling";

export function useStaffDirectory(clientRef: RefObject<GatewayClient | null>, scope: string, enabled: boolean) {
  const [state, setState] = useState<{ client: GatewayClient | null; scope: string; value: StaffSnapshot | null; error: string }>({ client: null, scope: "", value: null, error: "" });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    if (!enabled) { setState({ client: null, scope, value: null, error: "" }); return; }
    const client = clientRef.current;
    if (!client) return;
    const read = async () => {
      try {
        const value = await client.readStaff();
        if (active && clientRef.current === client) setState({ client, scope, value, error: "" });
      } catch {
        if (active && clientRef.current === client) setState(previous => ({ client, scope,
          value: previous.client === client && previous.scope === scope ? previous.value : null, error: "部门名录暂不可读取" }));
      }
    };
    const stop = startPolling(read, { intervalMs: 30000, hiddenIntervalMs: 120000, immediate: true, visibility: document });
    return () => { active = false; stop(); };
  }, [clientRef, scope, enabled, revision]);
  const current = state.client === clientRef.current && state.scope === scope;
  return { value: current ? state.value : null, error: current ? state.error : "", refresh: () => setRevision(value => value + 1) };
}
