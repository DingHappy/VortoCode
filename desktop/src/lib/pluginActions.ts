// 助理身份页「插件」区（日历、Notion…）的读取、保存凭据、检查与移除。
//
// 与 assistantPanelActions 同一条规矩：发请求时记下当时的 client 和 scope，结果回来时两者都没变
// 才写回界面——旧电脑的插件状态不能出现在新电脑的身份页里。检查结果只留在本次查看里（按 scope 标记）。
import type { Dispatch, SetStateAction } from "react";

import type { GatewayClient } from "../gateway";
import type { AssistantPlugin, PluginCheck } from "../types";
import { errorText } from "./errorText";

export type PluginSnapshot = { scope: string; items: AssistantPlugin[] };
export type PluginChecks = { scope: string; items: Record<string, PluginCheck> };

export interface PluginActionDeps {
  /** 此刻前台的 client（clientRef.current） */
  client: () => GatewayClient | null;
  /** 此刻的助理 scope（assistantScopeRef.current） */
  scope: () => string;
  /** 这次渲染时的 scope：点下去那一刻绑定的就是它 */
  renderScope: string;
  confirm: (text: string) => Promise<boolean>;
  setBanner: (text: string) => void;
  setPlugins: (snapshot: PluginSnapshot) => void;
  setChecks: Dispatch<SetStateAction<PluginChecks | null>>;
  setBusy: (name: string) => void;
}

export async function refreshPlugins(deps: Pick<PluginActionDeps, "client" | "scope" | "setPlugins">,
  client: GatewayClient, scope: string) {
  const items = await client.listPlugins();
  if (deps.client() === client && deps.scope() === scope) deps.setPlugins({ scope, items });
}

export function pluginActions(deps: PluginActionDeps) {
  const bind = () => {
    const client = deps.client();
    const scope = deps.renderScope;
    const current = () => deps.client() === client && deps.scope() === scope;
    return { client, scope, current };
  };

  const save = async (plugin: AssistantPlugin, credential: string): Promise<boolean> => {
    const { client, scope, current } = bind();
    if (!client || !current()) return false;
    deps.setBusy(plugin.name);
    try {
      const { check } = await client.savePluginCredential(plugin.name, credential);
      if (!current()) return false;
      deps.setChecks((prev) => ({ scope, items: { ...(prev?.scope === scope ? prev.items : {}), [plugin.name]: check } }));
      await refreshPlugins(deps, client, scope);
      return true;
    } catch (error) {
      if (current()) deps.setBanner(errorText(error, "保存凭据失败"));
      return false;
    } finally {
      if (current()) deps.setBusy("");
    }
  };

  const act = async (action: "check" | "remove", plugin: AssistantPlugin) => {
    const { client, scope, current } = bind();
    if (!client || !current()) return;
    if (action === "remove" && !await deps.confirm(`移除「${plugin.title}」的凭据？\n\n会删掉助理所在电脑上的凭据文件，插件立即停用；要再用得重新放凭据。`)) return;
    deps.setBusy(plugin.name);
    try {
      if (action === "check") {
        const result = await client.checkPlugin(plugin.name);
        if (current()) deps.setChecks((prev) => ({ scope, items: { ...(prev?.scope === scope ? prev.items : {}), [plugin.name]: result } }));
      } else {
        await client.removePluginCredential(plugin.name);
        if (current()) deps.setChecks((prev) => {
          const items = { ...(prev?.scope === scope ? prev.items : {}) };
          delete items[plugin.name];
          return { scope, items };
        });
        if (current()) await refreshPlugins(deps, client, scope);
      }
    } catch (error) {
      if (current()) deps.setBanner(errorText(error, action === "check" ? "检查插件失败" : "移除凭据失败"));
    } finally {
      if (current()) deps.setBusy("");
    }
  };

  return { save, act };
}
