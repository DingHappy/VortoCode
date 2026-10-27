import { Laptop, Smartphone, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { ConversationMessage, PhoneChat } from "../types";
import { useConversationLocation } from "../hooks/useConversationLocation";
import { useDialogSurface } from "../hooks/useDialogSurface";
import { ChatHistory } from "./ChatHistory";
import { ConversationLocation } from "./ConversationLocation";
import "./PhoneChatDialog.css";

export function phoneConversationMessages(chat: PhoneChat): ConversationMessage[] {
  const occurrences = new Map<string, number>();
  return chat.transcript.map((item, index) => {
    const role = item.role === "user" ? "user" as const : "assistant" as const;
    const rid = typeof item.rid === "string" && item.rid ? item.rid : undefined;
    const key = JSON.stringify([rid ?? index, role]);
    const ordinal = occurrences.get(key) ?? 0;
    occurrences.set(key, ordinal + 1);
    return { id: JSON.stringify([chat.sid, key, ordinal]), role, text: String(item.text ?? ""), ...(rid ? { rid } : {}) };
  });
}

/** Read recorded IM history without attaching Desktop to the phone's live session.
 * variant="offline"：常驻电脑连不上时在本机聊的，同样只读，底部放「带回常驻电脑」等操作。 */
export function PhoneChatDialog({ chat, onClose, focusedRid, owner, name = "VortoCode", variant = "phone", actions }: {
  chat: PhoneChat; onClose: () => void; focusedRid?: string; owner?: object | null; name?: string;
  variant?: "phone" | "offline"; actions?: ReactNode;
}) {
  const offline = variant === "offline";
  const dialog = useDialogSurface(), heading = useId();
  const fallback = useRef({});
  const client = owner === undefined ? fallback.current : owner;
  const container = useRef<HTMLDivElement>(null), autoScroll = useRef(true);
  const messages = useMemo(() => phoneConversationMessages(chat), [chat.sid, chat.transcript]);
  const location = useConversationLocation({ client, sid: chat.sid, messages, container, autoScroll });
  useEffect(() => {
    if (client && focusedRid) location.locate({ client, sid: chat.sid, rid: focusedRid });
    else { location.reset(); autoScroll.current = true; }
  }, [client, chat.sid, focusedRid, location.locate, location.reset]);
  useEffect(() => {
    if (location.rid || !autoScroll.current) return;
    const frame = requestAnimationFrame(() => { if (container.current) container.current.scrollTop = container.current.scrollHeight; });
    return () => cancelAnimationFrame(frame);
  }, [messages, location.rid]);
  const content = <dialog ref={dialog} className="phone-chat-dialog" aria-labelledby={heading}
    onKeyDown={event => { if (event.key === "Escape") event.stopPropagation(); }}
    onCancel={event => { event.preventDefault(); onClose(); }}
    onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <header><h2 id={heading}>{offline ? <Laptop size={16} /> : <Smartphone size={16} />}
      {offline ? `${chat.title || "本机对话"} · 本机离线时的聊天` : `${chat.title.replace(/^📱\s*/, "")} · 手机上的聊天`}</h2>
      <button aria-label={offline ? "关闭本机聊天" : "关闭手机聊天"} onClick={onClose}><X size={16} /></button></header>
    <p className="phone-chat-note">{offline
      ? "常驻电脑连不上时在这台 Mac 上聊的，只存在本机，常驻助理不知道。要让它知道，点「带回常驻电脑」：这段记录会作为附件放进输入框，你补一句话再发。"
      : "这里只读。要接着聊，请在手机上回复。较早消息可能不在当前已读取的记录中。"}</p>
    <div className="phone-chat-content">
      <ConversationLocation status={location.status} onLatest={location.latest} onRetry={location.retry} />
      <div className="phone-chat-messages" ref={container} tabIndex={-1} onScroll={event => {
        const node = event.currentTarget;
        autoScroll.current = node.scrollHeight - node.scrollTop - node.clientHeight < 96;
      }}>
        {messages.length === 0 && <p className="assistant-memory-empty">当前没有可显示的聊天记录。</p>}
        <ChatHistory messages={messages} activityGroups={new Map()} busy={false} activeTurnRid={null}
          assistant name={name} avatar={null} focusedRid={location.rid} />
      </div>
    </div>
    {actions && <footer className="phone-chat-actions">{actions}</footer>}
  </dialog>;
  return typeof document === "undefined" ? content : createPortal(content, document.body);
}
