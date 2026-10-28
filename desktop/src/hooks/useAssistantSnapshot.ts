// 助理模式右侧面板与对话卡片读到的「助理那台电脑上的东西」：回合、任务单、Codex 动作、手机聊天、
// 身份/头像、定时、记忆与提议、连接器，以及动态各来源的读取状态。
//
// 全部绑定在助理 scope（baseUrl:repoRoot:runtimeId:connection）上：scope 一变就清空并作废进行中的读取，
// 读回来时 client 与 scope 都没变才写入——旧电脑的数据不能出现在新电脑的面板里。
// 面板上的选中/弹窗等界面状态不在这里，由 App 在同一时机自行清掉。
import { useCallback, useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

import type { GatewayClient } from "../gateway";
import type { ActivityRead, ActivitySourceName } from "../lib/activitySources";
import { loadAssistantData } from "../lib/assistantData";
import { AssistantRefreshQueue } from "../lib/assistantRefresh";
import { ConnectorStatusReads } from "../lib/connectorStatus";
import { startPolling } from "../lib/polling";
import type {
  AgentTurn, AssistantAvatar, AssistantConnector, AssistantDispatch, AssistantMemory, AssistantProfile,
  AssistantScheduleItem, CodexAction, ConnectionState, MemoryProposal, PhoneChat,
} from "../types";

const DEFAULT_PROFILE: AssistantProfile = { name: "VortoCode", soul: "", about_you: "" };

export function useAssistantSnapshot({ clientRef, assistantScope, assistantScopeRef, connection, connectorsOpen }: {
  clientRef: RefObject<GatewayClient | null>;
  assistantScope: string;
  assistantScopeRef: RefObject<string>;
  connection: ConnectionState;
  /** 「定制」页开着：读连接的服务、授权中轮询 */
  connectorsOpen: boolean;
}) {
  // 助理的名字 / 关于你，以及它记住的事（都在助理所在的那台电脑上）
  const [assistantProfile, setAssistantProfile] = useState<AssistantProfile>(DEFAULT_PROFILE);
  const [assistantAvatar, setAssistantAvatar] = useState<AssistantAvatar | null>(null);
  const scheduleReadEpoch = useRef(0);
  const [assistantSchedule, setAssistantSchedule] = useState<AssistantScheduleItem[]>([]);
  const [assistantMemories, setAssistantMemories] = useState<AssistantMemory[]>([]);
  const [memoryProposals, setMemoryProposals] = useState<MemoryProposal[]>([]);
  // 设备授权期间只轮询本机凭据状态；这不代表当前会话 MCP 已连接。
  const [connectorSnapshot, setConnectors] = useState<AssistantConnector[]>([]);
  const [connectorSnapshotScope, setConnectorSnapshotScope] = useState<string | null>(null);
  const connectorReads = useRef(new ConnectorStatusReads());
  // 手机上和助理的聊天：只读查看（不接管——两个进程同时写一段对话会互相覆盖）
  const [phoneChats, setPhoneChats] = useState<PhoneChat[]>([]);
  const [agentTurns, setAgentTurns] = useState<AgentTurn[]>([]);
  const [activityReads, setActivityReads] = useState<{ client: GatewayClient; scope: string; sources: Partial<Record<ActivitySourceName, ActivityRead>> } | null>(null);
  const publishActivityRead = useCallback((client: GatewayClient, scope: string, name: ActivitySourceName, state: ActivityRead) => {
    if (clientRef.current !== client || assistantScopeRef.current !== scope) return;
    setActivityReads(previous => {
      if (clientRef.current !== client || assistantScopeRef.current !== scope) return previous;
      const sources = previous?.client === client && previous.scope === scope ? previous.sources : {};
      const prior = sources[name] ?? { status: "loading" as const };
      const next = state.status === "loading" ? { ...prior, checking: true } : { ...prior, ...state, checking: false };
      return { client, scope, sources: { ...sources, [name]: next } };
    });
  }, [clientRef, assistantScopeRef]);
  const connectors = connectorSnapshotScope === assistantScope ? connectorSnapshot : [];
  const connectorAuthorizing = connectors.some((item) => item.authorizing);
  const refreshConnectorStatus = useCallback((client: GatewayClient, scope: string) => {
    const current = () => clientRef.current === client && assistantScopeRef.current === scope;
    return connectorReads.current.read(client, scope, current, (items) => {
      setConnectors(items); setConnectorSnapshotScope(scope);
    });
  }, [clientRef, assistantScopeRef]);
  useEffect(() => {
    const client = clientRef.current;
    if (client && connection === "connected" && connectorsOpen) {
      void refreshConnectorStatus(client, assistantScope).catch(() => undefined);
    }
  }, [connectorsOpen, assistantScope, connection, refreshConnectorStatus, clientRef]);
  useEffect(() => {
    const client = clientRef.current;
    if (!client || connection !== "connected" || !connectorAuthorizing || !connectorsOpen) return;
    return startPolling(() => refreshConnectorStatus(client, assistantScope), {
      intervalMs: 4_000, hiddenIntervalMs: 15_000, visibility: document,
    });
  }, [connectorAuthorizing, connectorsOpen, assistantScope, connection, refreshConnectorStatus, clientRef]);
  const [dispatchSnapshot, setDispatches] = useState<AssistantDispatch[]>([]);
  const [codexActions, setCodexActions] = useState<CodexAction[]>([]);
  const [dispatchSnapshotScope, setDispatchSnapshotScope] = useState<string | null>(null);
  const dispatches = dispatchSnapshotScope === assistantScope ? dispatchSnapshot : [];
  const assistantLoadEpoch = useRef(0);
  const assistantRefreshQueue = useRef(new AssistantRefreshQueue());
  useEffect(() => {
    assistantLoadEpoch.current += 1;
    setAgentTurns([]); setDispatches([]); setCodexActions([]); setDispatchSnapshotScope(null); setPhoneChats([]);
    setAssistantProfile(DEFAULT_PROFILE); setAssistantAvatar(null);
    setAssistantSchedule([]); setAssistantMemories([]); setMemoryProposals([]);
    setConnectors([]); setConnectorSnapshotScope(null);
  }, [assistantScope]);

  const refreshAgentTurns = useCallback(async (refreshId?: string) => {
    const client = clientRef.current;
    if (!client) return;
    const scope = assistantScopeRef.current;
    const current = () => clientRef.current === client && assistantScopeRef.current === scope;
    const request = assistantRefreshQueue.current.request(client, scope, refreshId, current, async () => {
      const epoch = ++assistantLoadEpoch.current;
      await loadAssistantData(client, () => current() && assistantLoadEpoch.current === epoch,
        { turns: setAgentTurns,
          dispatches: items => { setDispatches(items); setDispatchSnapshotScope(scope); },
          dispatchError: () => setDispatches(previous => previous.map(item => ({ ...item,
            sync_state: "unreachable", sync_error: "无法读取最新任务单，请恢复连接后核对" }))),
          profile: setAssistantProfile, chats: setPhoneChats, avatar: setAssistantAvatar,
          codexActions: setCodexActions, sourceRead: (name, state) => publishActivityRead(client, scope, name, state) }, refreshId);
    });
    if (refreshId) await request;
    else await request.catch(() => {}); // Background polling reports failures through snapshot freshness.
  }, [publishActivityRead, clientRef, assistantScopeRef]);

  /** 保存身份前作废进行中的读取：保存前发出的读不能把旧身份写回来。 */
  const invalidateAssistantReads = useCallback(() => { assistantLoadEpoch.current += 1; }, []);

  return {
    assistantProfile, assistantAvatar, setAssistantAvatar, scheduleReadEpoch, assistantSchedule, setAssistantSchedule,
    assistantMemories, setAssistantMemories, memoryProposals, setMemoryProposals,
    connectors, connectorReads, refreshConnectorStatus, phoneChats, agentTurns, activityReads, publishActivityRead,
    dispatches, dispatchSnapshotScope, codexActions, refreshAgentTurns, setAssistantProfile, invalidateAssistantReads,
  };
}
