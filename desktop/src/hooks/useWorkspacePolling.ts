// 连上 runtime 后的定时刷新，频率集中在这一处。换了 runtime（runtimeId 变）就按新 runtime 重排。
// 隔离工作区才有的（控制台、PR 交付）在通用会话里不轮询。startPolling 在页面隐藏时放慢、
// 读取失败时退避；PR 交付与日志快照是固定间隔的后台刷新，不退避。
// 传入的 refresh 都是 App 的 useCallback（恒等稳定）；别在调用处包一层箭头函数，否则每次渲染都会重排轮询。
import { useEffect } from "react";

import { startPolling } from "../lib/polling";
import type { ConnectionState, WorkspaceScope } from "../types";

export function useWorkspacePolling({ connection, activeScope, runtimeId, refreshRuns, refreshSessions, refreshDecisions,
  refreshAudit, refreshPrDelivery, snapshotTodayJournal }: {
  connection: ConnectionState;
  activeScope: WorkspaceScope;
  runtimeId: string | undefined;
  refreshRuns: () => Promise<boolean>;
  refreshSessions: () => Promise<boolean>;
  refreshDecisions: () => Promise<boolean>;
  refreshAudit: () => Promise<boolean>;
  refreshPrDelivery: () => Promise<unknown>;
  snapshotTodayJournal: () => Promise<unknown>;
}) {
  // 控制台命令的输出要跟得上：0.9 秒一轮。
  useEffect(() => {
    if (connection !== "connected" || activeScope === "general") return undefined;
    return startPolling(refreshRuns, { intervalMs: 900, hiddenIntervalMs: 15_000, visibility: document });
  }, [activeScope, connection, runtimeId, refreshRuns]);

  useEffect(() => {
    if (connection !== "connected") return undefined;
    return startPolling(refreshSessions, { intervalMs: 2_500, hiddenIntervalMs: 10_000, visibility: document });
  }, [connection, runtimeId, refreshSessions]);

  useEffect(() => {
    if (connection !== "connected" || activeScope === "general") return undefined;
    const timer = window.setInterval(() => void refreshPrDelivery(), 20_000);
    return () => window.clearInterval(timer);
  }, [activeScope, connection, refreshPrDelivery]);

  useEffect(() => {
    if (connection !== "connected") return undefined;
    return startPolling(async () => {
      const results = await Promise.all([refreshDecisions(), refreshAudit()]);
      return results.some(Boolean);
    }, { intervalMs: 4_000, hiddenIntervalMs: 15_000, visibility: document });
  }, [connection, runtimeId, refreshAudit, refreshDecisions]);

  useEffect(() => {
    if (connection !== "connected") return undefined;
    const timer = window.setInterval(() => void snapshotTodayJournal(), 30_000);
    return () => window.clearInterval(timer);
  }, [connection, snapshotTodayJournal]);
}
