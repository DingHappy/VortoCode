import { useRef, useState } from "react";
import type { ChatCard, TeamStatus } from "../types";
import "./TeamChat.css";

const NOW: Partial<Record<TeamStatus, string>> = {
  active: "小组已经接着在做了", waiting: "小组现在在等你决定", review: "", done: "你已验收", failed: "小组失败了", cancelled: "小组已停止",
};

/** 主对话里那条「任务小组交付了 / 等你决定 / 失败了」下面的按钮（ADR-014：主人只面对主 agent）。
 * 正文是服务端代码拼的；这里只按小组的**最新**状态给固定动作：打开群聊、验收通过、退回（退回要写原因，去群聊里写）。 */
export function TeamNoteActions({ card, status, connected, onOpen, onAccept }: {
  card: ChatCard;
  /** 侧栏轮询到的最新状态；读不到时按消息写下时的状态 */
  status?: TeamStatus;
  connected: boolean;
  onOpen: (id: string) => void;
  onAccept: (id: string) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(false);
  const current = status ?? (card.status as TeamStatus);
  const changed = current !== card.status ? NOW[current] : "";
  const accept = async () => {
    if (pending.current || !connected) return;
    pending.current = true; setBusy(true); setError("");
    try { await onAccept(card.id); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "验收没有成功，请在群聊里核对"); }
    finally { pending.current = false; setBusy(false); }
  };
  return <div className="team-note-actions" aria-label="任务小组">
    {changed && <small role="status">{changed}</small>}
    <div className="task-actions">
      <button onClick={() => onOpen(card.id)}>打开群聊</button>
      {current === "review" && <button disabled={busy || !connected} onClick={() => void accept()}>验收通过</button>}
      {current === "review" && <button disabled={busy} onClick={() => onOpen(card.id)}>退回…</button>}
    </div>
    {!connected && current === "review" && <small>连接中断，恢复后可以验收。</small>}
    {error && <p role="alert" className="task-error">{error}</p>}
  </div>;
}
