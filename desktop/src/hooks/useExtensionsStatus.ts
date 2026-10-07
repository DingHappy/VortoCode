// 项目 Hook 信任状态 + 扩展清单（沿用 useJournal 的 hook 模式与三条红线）。
//
// toggleHookTrust 还要刷新审计域，属跨域写入，留在 App，用这里归还的 setHookStatus /
// setHookTrustBusy。两个 refresh 被 App 的 useCallback 依赖，必须恒等稳定（只依赖 clientRef）。
import { useCallback, useState } from "react";
import type { RefObject } from "react";

import type { GatewayClient } from "../gateway";
import type { ExtensionsInspectSnapshot, HookConfigStatus } from "../types";

export function useExtensionsStatus(clientRef: RefObject<GatewayClient | null>) {
  const [hookStatus, setHookStatus] = useState<HookConfigStatus | null>(null);
  const [hookTrustBusy, setHookTrustBusy] = useState(false);
  const [extensionsInspect, setExtensionsInspect] = useState<ExtensionsInspectSnapshot | null>(null);
  const [extensionsInspectBusy, setExtensionsInspectBusy] = useState(false);
  // extensionsInspectFilter（类型筛选 tab）是纯本地 UI 态，已下移到 <ExtensionsInspector> 自持。

  const refreshHookStatus = useCallback(async () => {
    const client = clientRef.current;
    if (!client) return;
    try {
      setHookStatus(await client.getHookStatus());
    } catch {
      setHookStatus(null);
    }
  }, [clientRef]);

  const refreshExtensionsInspect = useCallback(async () => {
    const client = clientRef.current;
    if (!client) return;
    setExtensionsInspectBusy(true);
    try {
      setExtensionsInspect(await client.getExtensionsInspect());
    } catch {
      setExtensionsInspect(null);
    } finally {
      setExtensionsInspectBusy(false);
    }
  }, [clientRef]);

  return {
    hookStatus, setHookStatus, hookTrustBusy, setHookTrustBusy,
    extensionsInspect, extensionsInspectBusy,
    refreshHookStatus, refreshExtensionsInspect,
  };
}
