// 项目 Hook 信任状态 + 扩展清单（沿用 useJournal 的 hook 模式与三条红线）。
//
// toggleHookTrust 还要刷新审计域，属跨域写入，留在 App，用这里归还的 setHookStatus /
// setHookTrustBusy。两个 refresh 被 App 的 useCallback 依赖，必须恒等稳定（只依赖 clientRef）。
import { useCallback, useRef, useState } from "react";
import type { RefObject } from "react";

import type { GatewayClient } from "../gateway";
import type { ExtensionsInspectSnapshot, HookConfigStatus } from "../types";

export function useExtensionsStatus(clientRef: RefObject<GatewayClient | null>, scope = "") {
  type Owner = { client: GatewayClient | null; epoch: number | undefined; scope: string };
  type Snapshot<T> = Owner & { value: T };
  const scopeRef = useRef(scope); scopeRef.current = scope;
  const owner = useCallback(() => ({ client: clientRef.current, epoch: clientRef.current?.connectionEpoch, scope: scopeRef.current }), [clientRef]);
  const current = useCallback((source: Owner | null) => Boolean(source && source.client === clientRef.current
    && source.epoch === clientRef.current?.connectionEpoch && source.scope === scopeRef.current), [clientRef]);
  const [hooks, setHooks] = useState<Snapshot<HookConfigStatus | null> | null>(null);
  const [hookBusy, setHookBusy] = useState<Snapshot<boolean> | null>(null);
  const [extensions, setExtensions] = useState<Snapshot<ExtensionsInspectSnapshot | null> | null>(null);
  const [extensionsBusy, setExtensionsBusy] = useState<Snapshot<boolean> | null>(null);
  const hookRead = useRef(0), extensionRead = useRef(0);
  const setHookStatus = useCallback((value: HookConfigStatus | null) => setHooks({ ...owner(), value }), [owner]);
  const setHookTrustBusy = useCallback((value: boolean) => setHookBusy({ ...owner(), value }), [owner]);
  const setExtensionsInspect = useCallback((value: ExtensionsInspectSnapshot | null) => setExtensions({ ...owner(), value }), [owner]);
  const refreshHookStatus = useCallback(async () => {
    const source = owner(), ticket = ++hookRead.current;
    if (!source.client) return;
    const publish = (value: HookConfigStatus | null) => { if (current(source) && ticket === hookRead.current) setHooks({ ...source, value }); };
    try { publish(await source.client.getHookStatus()); }
    catch { publish(null); }
  }, [owner, current]);
  const refreshExtensionsInspect = useCallback(async () => {
    const source = owner(), ticket = ++extensionRead.current;
    if (!source.client) return;
    const active = () => current(source) && ticket === extensionRead.current;
    setExtensionsBusy({ ...source, value: true });
    try {
      const value = await source.client.getExtensionsInspect();
      if (active()) setExtensions({ ...source, value });
    } catch { if (active()) setExtensions({ ...source, value: null }); }
    finally { if (active()) setExtensionsBusy({ ...source, value: false }); }
  }, [owner, current]);
  return {
    hookStatus: current(hooks) ? hooks!.value : null, setHookStatus,
    hookTrustBusy: current(hookBusy) && Boolean(hookBusy?.value), setHookTrustBusy,
    extensionsInspect: current(extensions) ? extensions!.value : null, setExtensionsInspect,
    extensionsInspectBusy: current(extensionsBusy) && Boolean(extensionsBusy?.value),
    refreshHookStatus, refreshExtensionsInspect,
  };
}
