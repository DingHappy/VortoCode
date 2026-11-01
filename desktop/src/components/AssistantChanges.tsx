import { useCallback, useEffect, useRef, useState } from "react";
import type { GatewayClient } from "../gateway";
import type { AssistantChange, AssistantUndoResult } from "../types";

export type AssistantChangeClient = Pick<GatewayClient, "listAssistantChanges" | "undoAssistantChange">;

const STATUS_TEXT: Record<AssistantChange["status"], string> = {
  done: "已完成，可撤销",
  undoing: "正在撤销",
  undone: "已撤销",
  failed: "撤销没成功，请核对目标卡",
};

/** 「我替你做了什么」：助理免确认改过的你的事项，逐条可撤（ADR-011）。撤不回的下游影响照实列出。 */
export function AssistantChanges({ client, connected, version, onChanged }: {
  client: AssistantChangeClient | null | undefined; connected: boolean;
  /** 目标列表每次重读后都会变：助理刚在对话里改过事项，这里跟着重读。 */
  version?: number;
  onChanged?: () => void;
}) {
  const [changes, setChanges] = useState<AssistantChange[] | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState("");
  const [result, setResult] = useState<{ id: string; outcome: AssistantUndoResult } | null>(null);
  const current = useRef(client);
  current.current = client;

  const refresh = useCallback(async () => {
    if (!client || !connected) return;
    try {
      const items = await client.listAssistantChanges();
      if (current.current === client) { setChanges(items); setError(""); }
    } catch {
      if (current.current === client) setError("改动记录暂时读不到，不能据此判断没有改动。");
    }
  }, [client, connected]);

  useEffect(() => { setChanges(null); setResult(null); setError(""); }, [client]);
  useEffect(() => { void refresh(); }, [refresh, version]);

  async function undo(change: AssistantChange) {
    if (!client || pending) return;
    setPending(change.id);
    try {
      const outcome = await client.undoAssistantChange(change.id);
      if (current.current === client) setResult({ id: change.id, outcome });
      onChanged?.();
    } catch (failure) {
      if (current.current === client) {
        setResult({ id: change.id, outcome: { ok: false, detail: failure instanceof Error ? failure.message : "撤销没成功", not_recallable: [] } });
      }
    } finally {
      setPending("");
      await refresh();
    }
  }

  if (!client || !connected || (changes !== null && changes.length === 0 && !error)) return null;
  const recent = (changes ?? []).slice().reverse();
  return <section className="assistant-section" aria-label="我替你做了什么">
    <h5>我替你做了什么</h5>
    {error && <p role="status">{error}</p>}
    {changes === null && !error && <p role="status">正在读取改动记录…</p>}
    <ul className="assistant-audit">
      {recent.map(change => <li key={change.id}>
        <span className="assistant-audit-text">
          <strong>{change.summary}</strong>
          <small>{STATUS_TEXT[change.status] ?? "状态待核对"}{change.tainted ? " · 读过外部内容后" : ""}</small>
          {result?.id === change.id && <span role="status">{result.outcome.detail}{result.outcome.not_recallable.map(item => `；${item}`).join("")}</span>}
          {result?.id !== change.id && change.status !== "done" && change.undo_detail && <span>{change.undo_detail}{(change.not_recallable ?? []).map(item => `；${item}`).join("")}</span>}
        </span>
        {change.status === "done" && <button disabled={Boolean(pending)} onClick={() => void undo(change)}>
          {pending === change.id ? "撤销中…" : "撤销"}</button>}
      </li>)}
    </ul>
  </section>;
}
