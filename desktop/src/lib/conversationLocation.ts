export interface ConversationTarget<Client = object> { client: Client; sid: string; rid: string }

export function sameConversation<Client>(target: ConversationTarget<Client>, client: Client | null, sid: string) {
  return target.client === client && target.sid === sid;
}

/** Use data equality, never interpolate a recorded rid into a CSS selector. */
export function revealConversationTurn(container: HTMLElement, rid: string): boolean {
  const nodes = [...container.querySelectorAll<HTMLElement>("article[data-turn-rid]")]
    .filter(node => node.dataset.turnRid === rid);
  const node = nodes.find(item => item.dataset.messageRole === "user") ?? nodes[0];
  if (!node) return false;
  node.scrollIntoView({ block: "start", behavior: "auto" });
  node.focus({ preventScroll: true });
  return true;
}
