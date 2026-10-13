import type { GatewayClient } from "../gateway";
import type { AgentTurn, AssistantDispatch, AssistantProfile, PhoneChat } from "../types";

/** Independent requests may finish out of order; only the current host/attempt can publish. */
export async function loadAssistantData(client: GatewayClient, current: () => boolean, sinks: {
  turns: (items: AgentTurn[]) => void;
  dispatches: (items: AssistantDispatch[]) => void;
  dispatchError?: () => void;
  profile: (profile: AssistantProfile) => void;
  chats: (items: PhoneChat[]) => void;
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
  ]).then(results => {
    if (refreshId && current() && results[1].status === "rejected") throw results[1].reason;
  });
}
