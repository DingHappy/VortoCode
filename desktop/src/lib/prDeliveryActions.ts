import type { Dispatch, SetStateAction } from "react";
import type { GatewayClient } from "../gateway";
import type { PrDeliveryCheck, PrDeliveryCheckLog, PrDeliverySnapshot } from "../types";
import { ViewRequests } from "./viewRequests";
import { errorText } from "./errorText";

export function prDeliveryActions(deps: {
  client: () => Pick<GatewayClient, "getPrDelivery" | "getPrCheckLog"> | null;
  snapshot: () => PrDeliverySnapshot | null;
  setSnapshot: (snapshot: PrDeliverySnapshot | null) => void;
  setLogs: Dispatch<SetStateAction<Record<string, PrDeliveryCheckLog>>>;
  setLoading: (loading: boolean) => void;
  banner: (text: string) => void;
}) {
  const reads = new ViewRequests(deps.client);
  const refreshPrDelivery = async () => {
    const client = deps.client(); if (!client) return;
    const request = reads.capture("snapshot");
    deps.setLoading(true);
    try {
      const snapshot = await client.getPrDelivery();
      if (!request.current()) return;
      const previousSnapshot = deps.snapshot();
      deps.setSnapshot(snapshot);
      deps.setLogs(previous => previousSnapshot?.branch === snapshot.branch && previousSnapshot?.pr === snapshot.pr
        ? Object.fromEntries(Object.entries(previous).filter(([id]) => snapshot.failing_checks.some(check => check.id === id))) : {});
    } catch (error) {
      if (request.current()) deps.setSnapshot({ ok: false, branch: "", comments: [], checks: [], failing_checks: [],
        summary: { total: 0, failed: 0, pending: 0, passed: 0 }, error: errorText(error, "读取 PR/CI 状态失败") });
    } finally { if (request.current()) deps.setLoading(false); }
  };
  const loadPrCheckLog = async (check: PrDeliveryCheck): Promise<PrDeliveryCheckLog | null> => {
    const client = deps.client(); if (!client) return null;
    const request = reads.capture(`log:${check.id}`);
    const snapshot = deps.snapshot();
    const current = () => request.current() && snapshot?.branch === deps.snapshot()?.branch
      && snapshot?.pr === deps.snapshot()?.pr && Boolean(deps.snapshot()?.failing_checks.some(item => item.id === check.id));
    try {
      const result = await client.getPrCheckLog(check.id);
      if (!current()) return null;
      deps.setLogs(previous => ({ ...previous, [check.id]: result }));
      return result;
    } catch (error) { if (current()) deps.banner(errorText(error, "读取 CI 失败日志失败")); return null; }
  };
  const reset = () => { reads.invalidate(); deps.setSnapshot(null); deps.setLogs({}); deps.setLoading(false); };
  return { refreshPrDelivery, loadPrCheckLog, reset, invalidate: () => reads.invalidate() };
}
