import type { GatewayClient } from "../gateway";
import type { AgentTurn, AssistantAvatar, AssistantDispatch, AssistantProfile, PhoneChat } from "../types";

/** Independent requests may finish out of order; only the current host/attempt can publish. */
export async function loadAssistantData(client: GatewayClient, current: () => boolean, sinks: {
  turns: (items: AgentTurn[]) => void;
  dispatches: (items: AssistantDispatch[]) => void;
  dispatchError?: () => void;
  profile: (profile: AssistantProfile) => void;
  chats: (items: PhoneChat[]) => void;
  /** 头像：旧版常驻电脑没有这个接口时静默保持首字头像 */
  avatar?: (avatar: AssistantAvatar | null) => void;
}, refreshId?: string) {
  await Promise.allSettled([
    refreshId ? Promise.resolve() : client.listAgentActivity(100).then(items => { if (current()) sinks.turns(items); }),
    client.listDispatches(refreshId).then(items => { if (current()) sinks.dispatches(items); })
      .catch(error => {
        if (current()) {
          sinks.dispatchError?.();
          if (refreshId) throw error;
        }
      }),
    refreshId ? Promise.resolve() : client.getAssistantProfile().then(profile => { if (current()) sinks.profile(profile); }),
    refreshId ? Promise.resolve() : client.listPhoneChats().then(items => { if (current()) sinks.chats(items); }),
    refreshId || !sinks.avatar ? Promise.resolve() : client.getAssistantAvatar()
      .then(avatar => { if (current()) sinks.avatar?.(avatar); }).catch(() => undefined),
  ]).then(results => {
    if (refreshId && current() && results[1].status === "rejected") throw results[1].reason;
  });
}
