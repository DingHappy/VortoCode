// 托管 runtime 的界面状态（当前进程、进程列表、恢复记录）与监督轮询；进程操作本身在 lib/managedRuntimes。
//
// 两个 hook：useManagedRuntimes 在 App 开头持有状态（很多派生值要用到当前进程）；
// useRuntimeSupervision 挂在 App 定义了 disconnect 之后，按当前 runtimeId 每 2 秒（后台 10 秒）监督一轮。
// 意外退出时先交给 onUnexpectedStop（App 决定要不要断开），再读恢复记录并提示。
// onUnexpectedStop 变了（换了工作区范围）就重排轮询，与迁移前的依赖一致。
import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import { ManagedRuntimes } from "../lib/managedRuntimes";
import { startPolling } from "../lib/polling";
import { EMPTY_PROCESS } from "../lib/runtimeSupervision";
import type { GatewayProcessStatus, GatewayRecoveryRecord } from "../types";

export function useManagedRuntimes() {
  const [processStatus, setProcessStatus] = useState<GatewayProcessStatus>(EMPTY_PROCESS);
  const [runtimeProcesses, setRuntimeProcesses] = useState<GatewayProcessStatus[]>([]);
  const [runtimeRecoveries, setRuntimeRecoveries] = useState<GatewayRecoveryRecord[]>([]);
  const [recoveryRecord, setRecoveryRecord] = useState<GatewayRecoveryRecord | null>(null);
  const runtimesRef = useRef<ManagedRuntimes | null>(null);
  if (!runtimesRef.current) runtimesRef.current = new ManagedRuntimes(invoke, {
    setProcessStatus, setRuntimeProcesses, setRuntimeRecoveries, setRecoveryRecord,
  });
  const runtimes = runtimesRef.current;
  // 收件箱与 Codex 观察读取按 runtime 取令牌：给它们一个指向同一份表的稳定引用。
  const runtimeTokensRef = useRef(runtimes.tokens);

  return { runtimes, runtimeTokensRef, processStatus, runtimeProcesses, runtimeRecoveries, recoveryRecord };
}

export function useRuntimeSupervision({ runtimes, runtimeId, onUnexpectedStop, onBanner }: {
  runtimes: ManagedRuntimes;
  runtimeId: string | undefined;
  onUnexpectedStop: (status: GatewayProcessStatus) => Promise<void>;
  onBanner: (text: string) => void;
}) {
  useEffect(() => {
    let disposed = false;
    const stop = startPolling(async () => {
      const result = await runtimes.supervise(runtimeId, () => disposed, onUnexpectedStop);
      if (result === false) return false;
      if (result) onBanner(result);
      return undefined;
    }, { intervalMs: 2_000, hiddenIntervalMs: 10_000, visibility: document });
    return () => {
      disposed = true;
      stop();
    };
  // onBanner 是 App 的 setBanner（恒等稳定）。
  }, [onUnexpectedStop, runtimeId, runtimes]);
}
