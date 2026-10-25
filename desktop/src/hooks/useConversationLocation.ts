import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import type { ConversationMessage } from "../types";
import { revealConversationTurn, sameConversation, type ConversationTarget } from "../lib/conversationLocation";

type Request<Client> = ConversationTarget<Client> & { id: number; startedAt: number };
export type ConversationLocationStatus = "locating" | "located" | "missing";

export function useConversationLocation<Client extends object>({ client, sid, messages, container, autoScroll }: {
  client: Client | null; sid: string; messages: ConversationMessage[];
  container: RefObject<HTMLDivElement | null>; autoScroll: RefObject<boolean>;
}) {
  const generation = useRef(0);
  const handled = useRef<number | null>(null);
  const scope = useRef({ client, sid });
  scope.current = { client, sid };
  const [request, setRequest] = useState<Request<Client> | null>(null);
  const [result, setResult] = useState<{ id: number; status: ConversationLocationStatus } | null>(null);
  const current = !!request && sameConversation(request, client, sid);
  const reset = useCallback(() => { generation.current++; setRequest(null); setResult(null); }, []);
  const locate = useCallback((target: ConversationTarget<Client>) => {
    autoScroll.current = false;
    setRequest({ ...target, id: ++generation.current, startedAt: Date.now() });
    setResult(null);
  }, [autoScroll]);
  useEffect(() => {
    if (!request) return;
    if (!current) { reset(); return; }
    if (handled.current === request.id) return;
    const valid = () => generation.current === request.id && sameConversation(request, scope.current.client, scope.current.sid);
    const frame = requestAnimationFrame(() => {
      if (valid() && container.current && revealConversationTurn(container.current, request.rid)) {
        clearTimeout(timer);
        handled.current = request.id;
        setResult({ id: request.id, status: "located" });
      }
    });
    const timer = setTimeout(() => {
      if (valid() && handled.current !== request.id) {
        // Stop late history from unexpectedly moving the user's viewport.
        handled.current = request.id;
        setResult({ id: request.id, status: "missing" });
      }
    }, Math.max(0, request.startedAt + 10_000 - Date.now()));
    return () => { cancelAnimationFrame(frame); clearTimeout(timer); };
  }, [request, current, client, sid, messages, container, reset]);
  const latest = useCallback(() => {
    const before = scope.current;
    reset(); autoScroll.current = true;
    const ticket = generation.current;
    requestAnimationFrame(() => {
      if (generation.current !== ticket || scope.current.client !== before.client || scope.current.sid !== before.sid) return;
      const node = container.current;
      if (node) { node.scrollTop = node.scrollHeight; node.focus({ preventScroll: true }); }
    });
  }, [reset, autoScroll, container]);
  return { locate, reset, latest, rid: current ? request!.rid : null,
    status: current ? result?.id === request!.id ? result.status : "locating" as const : null,
    retry: () => { if (request && current) locate(request); } };
}
