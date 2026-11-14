import { Fragment, memo, useMemo, type ReactNode } from "react";
import { sourceLabel } from "../lib/mainChat";
import type { AssistantAvatar, ConversationMessage, TurnActivity } from "../types";
import { MarkdownMessage } from "./MarkdownMessage";
import { RoleAvatar } from "./RoleAvatar";
import { TurnTimeline } from "./TurnTimeline";

/** Shared by saved messages and the live streaming reply. */
export function MessageIdentity({ role, name = "VortoOne", avatar, source }: {
  role: ConversationMessage["role"]; name?: string; avatar?: AssistantAvatar | null; source?: string;
}) {
  return <><div className="message-avatar" aria-hidden="true"><RoleAvatar
    character={role === "assistant" && source !== "team" ? "general" : undefined}
    avatar={role === "assistant" && source !== "team" ? avatar : undefined} /></div>
    <div className="message-author"><strong>{role === "user" ? "你" : role === "assistant"
      ? source === "team" ? "任务小组" : name : "系统"}</strong>
      {role === "assistant" && source && source !== "team" && <span className="message-source">回复在手机上</span>}
    </div></>;
}

/** 流式正文与输入草稿更新时，历史消息不重新解析；时间线状态仍由原回合资料驱动。 */
export const ChatHistory = memo(function ChatHistory({ messages, activityGroups, busy, activeTurnRid,
  assistant, assistantName = "VortoOne", assistantAvatar, focusedRid, renderCard }: {
  messages: ConversationMessage[];
  activityGroups: Map<string, TurnActivity[]>;
  busy: boolean;
  activeTurnRid: string | null;
  assistant: boolean;
  assistantName?: string;
  assistantAvatar?: AssistantAvatar | null;
  focusedRid?: string | null;
  /** 代码写进对话的状态消息（如任务小组交付了）下面的按钮 */
  renderCard?: (message: ConversationMessage) => ReactNode;
}) {
  const outputRids = useMemo(() => new Set(messages
    .filter(message => message.role === "assistant" && message.text.trim().length > 0)
    .map(message => message.rid)), [messages]);
  return <>{messages.map(message => <Fragment key={message.id}>
    <article className={`message ${message.role}${focusedRid && message.rid === focusedRid ? " located-turn" : ""}`}
      data-turn-rid={message.rid} data-message-role={message.role}
      tabIndex={focusedRid && message.rid === focusedRid ? -1 : undefined}>
      {assistant ? <MessageIdentity role={message.role} name={assistantName} avatar={assistantAvatar} source={message.source} />
        : <div className="message-author">{message.role === "user" ? "你" : message.role === "assistant" ? "VortoOne" : "Runtime"}</div>}
      <div className="message-body">
        {message.role === "user" && sourceLabel(message.source) && <div className="message-source">{sourceLabel(message.source)}</div>}
        {message.images && message.images.length > 0 && <div className="message-images">
          {message.images.map((src, index) => <img key={index} src={src} alt={`发送的图片 ${index + 1}`} />)}
        </div>}
        {message.role === "assistant" ? <MarkdownMessage text={message.text} /> : message.text}
        {message.card && renderCard?.(message)}
      </div>
    </article>
    {message.role === "user" && message.rid && activityGroups.has(message.rid) && (
      <TurnTimeline items={activityGroups.get(message.rid)!} active={busy && activeTurnRid === message.rid}
        hasOutput={outputRids.has(message.rid)} />
    )}
  </Fragment>)}</>;
});
