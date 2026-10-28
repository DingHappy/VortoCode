// 助理右侧面板（定时、身份与记忆、连接器、头像）的操作。
//
// 每个操作都可能跨过一次「换了会话 / 换了常驻电脑」：发请求时记下当时的 client 和 scope，
// 结果回来时两者都没变才写回界面，否则丢掉——旧电脑的结果不能出现在新电脑的面板里。
// 定时列表另有读序号：并发读取时只认最后一次。
import type { Dispatch, SetStateAction } from "react";

import type { GatewayClient } from "../gateway";
import type {
  AssistantAvatar, AssistantConnector, AssistantMemory, AssistantScheduleItem, AssistantScheduleRun, MemoryProposal,
} from "../types";
import type { AssistantTab } from "../components/AssistantMode";
import type { AssistantActionGate } from "./assistantTask";
import { connectorDisconnectMessage } from "./connectorStatus";
import { errorText } from "./errorText";

export interface AssistantPanelDeps {
  /** 此刻前台的 client（clientRef.current） */
  client: () => GatewayClient | null;
  /** 此刻的助理 scope（assistantScopeRef.current） */
  scope: () => string;
  /** 这次渲染时的 scope：点下去那一刻绑定的就是它 */
  renderScope: string;
  scheduleEpoch: { current: number };
  connectorReads: { invalidate(): void };
  actionGate: AssistantActionGate;
  refreshConnectorStatus: (client: GatewayClient, scope: string) => Promise<unknown>;
  refreshAudit: () => Promise<unknown>;
  confirm: (text: string) => Promise<boolean>;
  openUrl: (url: string) => Promise<unknown>;
  copy: (text: string) => Promise<void>;
  prepareAvatar: (file: File) => Promise<AssistantAvatar>;
  setBanner: (text: string) => void;
  setSchedule: (items: AssistantScheduleItem[]) => void;
  setMemories: Dispatch<SetStateAction<AssistantMemory[]>>;
  setProposals: Dispatch<SetStateAction<MemoryProposal[]>>;
  setAvatar: (avatar: AssistantAvatar | null) => void;
}

export function assistantPanelActions(deps: AssistantPanelDeps) {
  const sameAs = (client: GatewayClient | null, scope: string) => () => deps.client() === client && deps.scope() === scope;

  /** 切到某个标签时顺手刷新它的数据。 */
  const tabOpened = (tab: AssistantTab) => {
    const client = deps.client();
    if (tab === "audit") void deps.refreshAudit();
    const scope = deps.scope();
    const current = sameAs(client, scope);
    if (tab === "schedule") {
      const epoch = ++deps.scheduleEpoch.current;
      void client?.getAssistantSchedule().then(items => { if (current() && epoch === deps.scheduleEpoch.current) deps.setSchedule(items); }).catch(error => { if (current()) deps.setBanner(String(error)); });
    }
    if (tab === "identity") {
      void client?.listAssistantMemories().then(items => { if (current()) deps.setMemories(items); }).catch(() => {});
      void client?.listMemoryProposals().then(items => { if (current()) deps.setProposals(items); }).catch(() => {});
    }
  };

  const scheduleRefresh = async () => {
    const client = deps.client();
    const scope = deps.renderScope;
    if (!client || deps.scope() !== scope) return;
    const epoch = ++deps.scheduleEpoch.current;
    const items = await client.getAssistantSchedule();
    if (deps.client() === client && deps.scope() === scope && epoch === deps.scheduleEpoch.current) deps.setSchedule(items);
  };

  const scheduleToggle = async (item: AssistantScheduleItem) => {
    const client = deps.client();
    const scope = deps.renderScope;
    const current = sameAs(client, scope);
    if (!client || !current() || !item.revision) return;
    const epoch = ++deps.scheduleEpoch.current;
    await client.setScheduleEnabled(item.title, !item.enabled, item.revision);
    if (!current()) return;
    // Reload every card: the revision covers the shared config file.
    try {
      const items = await client.getAssistantSchedule();
      if (current() && epoch === deps.scheduleEpoch.current) deps.setSchedule(items);
    } catch {
      if (current()) throw new Error("启停配置已保存，但刷新失败，请重新核对状态");
    }
  };

  const scheduleHistory = async (item: AssistantScheduleItem): Promise<AssistantScheduleRun[]> => {
    const client = deps.client();
    const scope = deps.renderScope;
    if (!client || deps.scope() !== scope) return [];
    const runs = await client.getScheduleHistory(item.title);
    if (deps.client() !== client || deps.scope() !== scope) return [];
    return runs;
  };

  const avatar = async (file: File | null) => {
    const client = deps.client();
    if (!client) return;
    try {
      if (file) deps.setAvatar(await client.saveAssistantAvatar(await deps.prepareAvatar(file)));
      else { await client.removeAssistantAvatar(); deps.setAvatar(null); }
    } catch (error) {
      deps.setBanner(errorText(error, "换头像失败"));
    }
  };

  const connector = async (action: "connect" | "disconnect" | "open" | "copy", item: AssistantConnector) => {
    const client = deps.client();
    const scope = deps.renderScope;
    const current = sameAs(client, scope);
    if (!client || !current()) return;
    try {
      await deps.actionGate.run(client, scope, `connector:${item.name}`, current, async () => {
        let receiptMessage = "";
        if (action === "copy" && item.authorizing) await deps.copy(item.authorizing.user_code);
        if (action === "open" && item.authorizing) await deps.openUrl(item.authorizing.verification_uri);
        if (action === "connect") {
          deps.connectorReads.invalidate();
          const started = await client.authorizeConnector(item.name);
          if (!current()) return;
          if (started) await deps.openUrl(started.verification_uri).catch(() => undefined);
        }
        if (action === "disconnect") {
          const prompt = item.local_revocation_supported
            ? "断开此连接器并移除本机凭据？\n\n将禁用本运行实例的后续调用，并尝试关闭本地连接。已发出的远端请求不会因此被确认停止；GitHub 授权仍需单独撤销。"
            : "移除本机 GitHub 凭据并取消待处理授权？\n\n当前运行版本尚未确认能关闭旧连接；GitHub 授权仍需单独撤销。";
          if (!await deps.confirm(prompt)) return;
          if (!current()) return;
          deps.connectorReads.invalidate();
          const receipt = await client.disconnectConnector(item.name);
          if (!current()) return;
          receiptMessage = connectorDisconnectMessage(receipt);
        }
        if (current() && (action === "connect" || action === "disconnect")) {
          deps.connectorReads.invalidate();
          try { await deps.refreshConnectorStatus(client, scope); }
          catch {
            if (current()) deps.setBanner((receiptMessage ? receiptMessage + "\n" : "") + "状态刷新失败，请重新核对。");
            return;
          }
          if (current() && receiptMessage) deps.setBanner(receiptMessage);
        }
      });
    } catch (error) {
      if (current()) deps.setBanner(errorText(error, "连接服务失败"));
    }
  };

  const decideProposal = async (proposal: MemoryProposal, approve: boolean, replace?: string[]) => {
    const client = deps.client();
    const current = sameAs(client, deps.scope());
    if (!client) return;
    try {
      await client.decideMemoryProposal(proposal.id, approve, replace ?? []);
      if (!current()) return;
      deps.setProposals((previous) => previous.filter((item) => item.id !== proposal.id));
      if (approve) {
        const memories = await client.listAssistantMemories();
        if (current()) deps.setMemories(memories);
      }
    } catch (error) {
      if (current()) deps.setBanner(errorText(error, "处理记忆候选失败"));
    }
  };

  const deleteMemory = async (memory: AssistantMemory) => {
    const client = deps.client();
    const current = sameAs(client, deps.scope());
    if (!client || !await deps.confirm(`忘掉这条记忆？\n\n${memory.content}`) || !current()) return;
    try {
      await client.deleteAssistantMemory(memory.id);
      if (current()) deps.setMemories((previous) => previous.filter((item) => item.id !== memory.id));
    } catch (error) {
      if (current()) deps.setBanner(errorText(error, "删除记忆失败"));
    }
  };

  return { tabOpened, scheduleRefresh, scheduleToggle, scheduleHistory, avatar, connector, decideProposal, deleteMemory };
}
