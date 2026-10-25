// 跨工作区收件箱：轮询每个运行端的 /api/runtime/inbox，汇总「要你处理」的数量。
//
// 读取有代次：来源集合（指纹）变了或卸载时作废进行中的一轮，迟到结果不发布；同一代次的并发
// 调用共用一个 promise。运行端令牌归连接域（App 的 runtimeTokensRef），这里只读。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";

import { GatewayClient } from "../gateway";
import { startPolling } from "../lib/polling";
import { summarizeRuntimeInboxes } from "../lib/runtimeInbox";
import {
  mergeRuntimeInboxes, observeRuntimeInboxes, runtimeInboxSourceKey as sourceKeyOf, runtimeInboxSources as sourcesOf,
  type RuntimeInboxSource,
} from "../lib/runtimeInboxReads";
import type { DecisionItem, DesktopProjectProfile, DesktopRuntimeInbox, GatewayProcessStatus } from "../types";

export function useRuntimeInboxes({ runtimeProcesses, projects, decisionItems, runtimeTokensRef }: {
  runtimeProcesses: GatewayProcessStatus[];
  projects: DesktopProjectProfile[];
  /** 当前工作区的 PR/CI 决策也算进「要你处理」。 */
  decisionItems: DecisionItem[];
  runtimeTokensRef: RefObject<Map<string, string>>;
}) {
  const [runtimeInboxes, setRuntimeInboxes] = useState<DesktopRuntimeInbox[]>([]);
  const runtimeInboxGenerationRef = useRef(0);
  const runtimeInboxKeyRef = useRef("");
  const runtimeInboxPendingRef = useRef<{ key: string; generation: number; promise: Promise<boolean> } | null>(null);
  const runtimeInboxSourcesRef = useRef<RuntimeInboxSource[]>([]);
  const runtimeFirstSeenRef = useRef<Map<string, number>>(new Map());
  const runtimeEverReachableRef = useRef<Set<string>>(new Set());

  const runtimeInboxSources = useMemo(() => sourcesOf(runtimeProcesses, projects), [projects, runtimeProcesses]);
  runtimeInboxSourcesRef.current = runtimeInboxSources;
  const runtimeInboxSourceKey = useMemo(() => sourceKeyOf(runtimeInboxSources), [runtimeInboxSources]);
  runtimeInboxKeyRef.current = runtimeInboxSourceKey;
  const runtimeInboxSummary = useMemo(() => {
    const summary = summarizeRuntimeInboxes(runtimeInboxes.map((item) => ({
      error: item.error,
      firstSeenAt: runtimeFirstSeenRef.current.get(item.runtimeId) ?? item.checkedAt,
      everReachable: runtimeEverReachableRef.current.has(item.runtimeId),
      counts: item.snapshot?.counts ?? null,
    })), Date.now());
    summary.actionable += decisionItems.filter((item) => item.kind === "pr_check" || item.kind === "pr_review").length;
    return summary;
  }, [decisionItems, runtimeInboxes]);

  const refreshRuntimeInboxes = useCallback((): Promise<boolean> => {
    const pending = runtimeInboxPendingRef.current;
    if (pending?.key === runtimeInboxSourceKey && pending.generation === runtimeInboxGenerationRef.current) return pending.promise;
    const generation = ++runtimeInboxGenerationRef.current;
    const current = () => generation === runtimeInboxGenerationRef.current && runtimeInboxSourceKey === runtimeInboxKeyRef.current;
    const sources = runtimeInboxSourcesRef.current;
    if (sources.length === 0) {
      setRuntimeInboxes([]);
      return Promise.resolve(true);
    }
    const checkedAt = Date.now();
    // 首见时间 + 连上过没有：用来把"还在启动"和"连上过又断了"分开（见 lib/runtimeInbox.ts）。
    sources.forEach((source) => {
      if (!runtimeFirstSeenRef.current.has(source.runtimeId)) {
        runtimeFirstSeenRef.current.set(source.runtimeId, checkedAt);
      }
    });
    setRuntimeInboxes(previous => current() ? mergeRuntimeInboxes(previous, sources) : previous);
    const promise = observeRuntimeInboxes(sources, source => {
      const client = new GatewayClient({
        baseUrl: source.baseUrl,
        token: runtimeTokensRef.current.get(source.runtimeId) ?? "",
        remoteProjectId: source.runtimeId.startsWith("remote:") ? source.projectId : undefined,
      });
      return client.getRuntimeInbox();
    }, current, item => {
      if (item.snapshot) runtimeEverReachableRef.current.add(item.runtimeId);
      setRuntimeInboxes(previous => current() ? mergeRuntimeInboxes(previous, sources, item) : previous);
    }).finally(() => {
      if (runtimeInboxPendingRef.current?.generation === generation) runtimeInboxPendingRef.current = null;
    });
    runtimeInboxPendingRef.current = { key: runtimeInboxSourceKey, generation, promise };
    return promise;
  }, [runtimeInboxSourceKey, runtimeTokensRef]);

  useEffect(() => {
    const stop = startPolling(refreshRuntimeInboxes, {
      intervalMs: 4_000, hiddenIntervalMs: 15_000, immediate: true, visibility: document,
    });
    return () => {
      runtimeInboxGenerationRef.current += 1;
      stop();
    };
  }, [refreshRuntimeInboxes]);

  return { runtimeInboxes, runtimeInboxSummary, refreshRuntimeInboxes };
}
