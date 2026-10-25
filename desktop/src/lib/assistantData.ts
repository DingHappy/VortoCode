import type { GatewayClient } from "../gateway";
import type { AgentTurn, AssistantAvatar, AssistantDispatch, AssistantProfile, CodexAction, PhoneChat } from "../types";
import type { ActivityRead, ActivitySourceName } from "./activitySources";

/** Independent requests may finish out of order; only the current host/attempt can publish. */
export async function loadAssistantData(client: GatewayClient, current: () => boolean, sinks: {
  turns: (items: AgentTurn[]) => void;
  dispatches: (items: AssistantDispatch[]) => void;
  dispatchError?: () => void;
  profile: (profile: AssistantProfile) => void;
  chats: (items: PhoneChat[]) => void;
  /** 头像：旧版常驻电脑没有这个接口时静默保持首字头像 */
  avatar?: (avatar: AssistantAvatar | null) => void;
  /** 助理拟好的 Codex 动作：旧版常驻电脑没有这个接口时保持空 */
  codexActions?: (items: CodexAction[]) => void;
  sourceRead?: (name: ActivitySourceName, state: ActivityRead) => void;
}, refreshId?: string) {
  const read = <T>(name: ActivitySourceName, request: () => Promise<T>, publish: (value: T) => void) => {
    if (current()) sinks.sourceRead?.(name, { status: "loading" });
    return Promise.resolve().then(request).then(value => {
      if (current()) { publish(value); sinks.sourceRead?.(name, { status: "current", checkedAt: Date.now() }); }
    }).catch(error => {
      if (!current()) return;
      sinks.sourceRead?.(name, { status: "unavailable" });
      if (name === "dispatches") { sinks.dispatchError?.(); if (refreshId) throw error; }
    });
  };
  await Promise.allSettled([
    refreshId ? Promise.resolve() : read("turns", () => client.listAgentActivity(100), sinks.turns),
    read("dispatches", () => client.listDispatches(refreshId), sinks.dispatches),
    refreshId ? Promise.resolve() : client.getAssistantProfile().then(profile => { if (current()) sinks.profile(profile); }),
    refreshId ? Promise.resolve() : client.listPhoneChats().then(items => { if (current()) sinks.chats(items); }),
    refreshId || !sinks.avatar ? Promise.resolve() : client.getAssistantAvatar()
      .then(avatar => { if (current()) sinks.avatar?.(avatar); }).catch(() => undefined),
    !sinks.codexActions ? Promise.resolve() : read("codexActions", () => client.listCodexActions(), sinks.codexActions),
  ]).then(results => {
    if (refreshId && current() && results[1].status === "rejected") throw results[1].reason;
  });
}
