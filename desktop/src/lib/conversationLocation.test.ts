import { expect, it, vi } from "vitest";
import { revealConversationTurn, sameConversation } from "./conversationLocation";

function anchor(rid: string, role: string) {
  return { dataset: { turnRid: rid, messageRole: role }, scrollIntoView: vi.fn(), focus: vi.fn() };
}
function container(nodes: ReturnType<typeof anchor>[]) {
  return { querySelectorAll: vi.fn().mockReturnValue(nodes) } as unknown as HTMLElement;
}
it("requires both the exact client owner and session, even when turn ids collide", () => {
  const a = {}, b = {}; const target = { client: a, sid: "one", rid: "same" };
  expect(sameConversation(target, a, "one")).toBe(true);
  expect(sameConversation(target, b, "one")).toBe(false);
  expect(sameConversation(target, a, "other")).toBe(false);
  expect(sameConversation(target, null, "one")).toBe(false);
});
it("reveals the original user message and leaves unrelated turns alone", () => {
  const reply = anchor("r", "assistant"), user = anchor("r", "user"), other = anchor("else", "user");
  expect(revealConversationTurn(container([reply, other, user]), "r")).toBe(true);
  expect(user.scrollIntoView).toHaveBeenCalledWith({ block: "start", behavior: "auto" });
  expect(user.focus).toHaveBeenCalledWith({ preventScroll: true });
  expect(reply.focus).not.toHaveBeenCalled(); expect(other.scrollIntoView).not.toHaveBeenCalled();
});
it("can locate a preserved reply when the user entry is absent, and never guesses another turn", () => {
  const reply = anchor("r", "assistant"); const root = container([reply]);
  expect(revealConversationTurn(root, "r")).toBe(true);
  expect(revealConversationTurn(root, "missing")).toBe(false);
  expect(reply.focus).toHaveBeenCalledTimes(1);
});
it("matches a recorded id as data instead of treating it as a selector", () => {
  const rid = 'r"] [data-turn-rid="other';
  const exact = anchor(rid, "user"), other = anchor("other", "user"); const root = container([exact, other]);
  expect(revealConversationTurn(root, rid)).toBe(true);
  expect(root.querySelectorAll).toHaveBeenCalledWith("article[data-turn-rid]");
  expect(exact.focus).toHaveBeenCalledTimes(1); expect(other.focus).not.toHaveBeenCalled();
});
