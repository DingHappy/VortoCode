// 助理身份页的插件区：打开身份页时读一次状态；异步操作委托给可单独验证的 pluginActions。
// 换了助理 scope 就清掉「正在处理」标记；快照与检查结果按 scope 标记，不属于当前 scope 的不显示。
import { useCallback, useEffect, useState } from "react";
import type { RefObject } from "react";

import type { AssistantTab } from "../components/AssistantMode";
import type { GatewayClient } from "../gateway";
import { confirmAction } from "../lib/confirm";
import { pluginActions, refreshPlugins as readPlugins, type PluginChecks, type PluginSnapshot } from "../lib/pluginActions";
import type { ConnectionState } from "../types";

export function usePlugins(
  clientRef: RefObject<GatewayClient | null>,
  assistantScopeRef: RefObject<string>,
  assistantScope: string,
  connection: ConnectionState,
  assistantTab: AssistantTab,
  onBanner: (text: string) => void,
) {
  const [pluginSnapshot, setPlugins] = useState<PluginSnapshot | null>(null);
  const [pluginChecks, setPluginChecks] = useState<PluginChecks | null>(null);
  const [pluginBusy, setPluginBusy] = useState("");
  const plugins = pluginSnapshot?.scope === assistantScope ? pluginSnapshot.items : [];
  const refreshPlugins = useCallback((client: GatewayClient, scope: string) => readPlugins({
    client: () => clientRef.current, scope: () => assistantScopeRef.current, setPlugins,
  }, client, scope), [clientRef, assistantScopeRef]);
  useEffect(() => {
    const client = clientRef.current;
    if (client && connection === "connected" && assistantTab === "identity") {
      void refreshPlugins(client, assistantScope).catch(() => undefined);   // 旧版服务没有这个接口：不显示插件区
    }
  }, [assistantTab, assistantScope, connection, refreshPlugins, clientRef]);
  useEffect(() => { setPluginBusy(""); }, [assistantScope]);
  const actions = pluginActions({
    client: () => clientRef.current, scope: () => assistantScopeRef.current, renderScope: assistantScope,
    confirm: confirmAction, setBanner: onBanner, setPlugins, setChecks: setPluginChecks, setBusy: setPluginBusy,
  });
  return {
    plugins, pluginBusy,
    pluginChecks: pluginChecks?.scope === assistantScope ? pluginChecks.items : {},
    savePlugin: actions.save,
    pluginAction: (action: "check" | "remove", plugin: Parameters<typeof actions.act>[1]) => void actions.act(action, plugin),
  };
}
