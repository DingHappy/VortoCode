import { Fragment, memo, useMemo } from "react";
import { sourceLabel } from "../lib/mainChat";
import type { AssistantAvatar, ConversationMessage, TurnActivity } from "../types";
import { AssistantAvatarImage } from "./AssistantMode";
import { MarkdownMessage } from "./MarkdownMessage";
import { TurnTimeline } from "./TurnTimeline";

/** 流式正文与输入草稿更新时，历史消息不重新解析；时间线状态仍由原回合资料驱动。 */
export const ChatHistory = memo(function ChatHistory({ messages, activityGroups, busy, activeTurnRid,
  assistant, name, avatar }: {
  messages: ConversationMessage[];
  activityGroups: Map<string, TurnActivity[]>;
  busy: boolean;
  activeTurnRid: string | null;
  assistant: boolean;
  name: string;
  avatar: AssistantAvatar | null;
}) {
  const outputRids = useMemo(() => new Set(messages
    .filter(message => message.role === "assistant" && message.text.trim().length > 0)
    .map(message => message.rid)), [messages]);
  return <>{messages.map(message => <Fragment key={message.id}>
    <article className={`message ${message.role}`}>
      {assistant && message.role === "assistant"
        ? <div className="message-author with-avatar"><AssistantAvatarImage name={name} avatar={avatar} size={22} />{name}
          {message.source && <span className="message-source">回复在手机上</span>}</div>
        : <div className="message-author">{message.role === "user" ? "你" : message.role === "assistant" ? "VortoCode" : "Runtime"}</div>}
      <div className="message-body">
        {message.role === "user" && sourceLabel(message.source) && <div className="message-source">{sourceLabel(message.source)}</div>}
        {message.images && message.images.length > 0 && <div className="message-images">
          {message.images.map((src, index) => <img key={index} src={src} alt={`发送的图片 ${index + 1}`} />)}
        </div>}
        {message.role === "assistant" ? <MarkdownMessage text={message.text} /> : message.text}
      </div>
    </article>
    {message.role === "user" && message.rid && activityGroups.has(message.rid) && (
      <TurnTimeline items={activityGroups.get(message.rid)!} active={busy && activeTurnRid === message.rid}
        hasOutput={outputRids.has(message.rid)} />
    )}
  </Fragment>)}</>;
});
