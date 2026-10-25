// App.tsx 抽出的对话记录映射族（与 activities.ts 同一做法，只搬家不改行为）。
// 把 agent_history / agent_queue / agent_say / agent_emit 等事件折叠进消息列表与活动时间线，
// 纯函数、无副作用；事件总线（handleProtocolEvent）仍留在 App.tsx，只是调用这些映射器。
import type { ConversationMessage, PromptQueueItem, ProtocolEvent, TurnActivity } from "../types";
import { upsertActivity } from "./activities";

export function messageId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function normalizePromptQueueItem(value: unknown, fallbackPosition = 0): PromptQueueItem | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Partial<PromptQueueItem>;
  if (!item.id || typeof item.text !== "string") return null;
  return {
    id: String(item.id),
    version: Number.isFinite(item.version) ? Number(item.version) : 0,
    text: item.text,
    mode: item.mode === "build" ? "build" : "plan",
    position: Number.isFinite(item.position) ? Number(item.position) : fallbackPosition,
    created_at: String(item.created_at ?? ""),
    context_count: Number.isFinite(item.context_count) ? Number(item.context_count) : 0,
  };
}

/** agent_queue：排队中的（丢掉不合法的条目）、正在跑的那条、是否暂停。 */
export function promptQueueFromEvent(event: ProtocolEvent): { queued: PromptQueueItem[]; running: PromptQueueItem | null; paused: boolean } {
  const queued = (event.items ?? [])
    .map((item, index) => normalizePromptQueueItem(item, index))
    .filter((item): item is PromptQueueItem => item !== null);
  return { queued, running: normalizePromptQueueItem(event.running), paused: event.paused === true };
}

/** agent_history：服务端存的对话记录换成界面消息；id 带上会话，换会话不会撞 key。 */
export function historyMessages(event: ProtocolEvent, sid: string): ConversationMessage[] {
  return ((event.items ?? []) as Array<{ role?: string; text?: string; rid?: string; source?: string }>).map((item, index) => ({
    id: `history-${index}-${sid}`,
    role: item.role === "user" ? "user" : "assistant",
    text: String(item.text ?? ""),
    rid: item.rid,
    ...(typeof item.source === "string" && item.source ? { source: item.source } : {}),
  }));
}

/** 「正在分析任务」阶段：同一回合只占一行，后续阶段事件按 id 覆盖它。 */
export function thinkingActivity(rid: string | undefined): TurnActivity {
  return {
    id: `${rid ?? "legacy"}:thinking`,
    rid,
    kind: "phase",
    status: "running",
    label: "正在分析任务",
    phase: "thinking",
  };
}

/** 正在跑的那条若是别的客户端发的（本地没有这条用户消息），补上一条。 */
export function withRunningPrompt(previous: ConversationMessage[], running: PromptQueueItem): ConversationMessage[] {
  return previous.some((message) => message.rid === running.id)
    ? previous
    : [...previous, {
        id: messageId("user"),
        role: "user",
        text: running.context_count > 0
          ? `${running.text}\n\n📎 ${running.context_count} 个源码引用`
          : running.text,
        rid: running.id,
      }];
}

/** agent_say 的旁白；以 🔧 开头且这回合已有工具活动时不重复记一行。 */
export function withSayActivity(previous: TurnActivity[], rid: string | undefined, label: string): TurnActivity[] {
  if (label.startsWith("🔧") && previous.some((item) => item.rid === rid && item.kind === "tool")) return previous;
  return upsertActivity(previous, {
    id: messageId(`event-${rid ?? "legacy"}`),
    rid,
    kind: "event",
    status: "completed",
    label,
  });
}

/** agent_emit：同一回合（或无 rid 时同一文本）的回复只记一次——重连重放不会出现两遍。 */
export function withAssistantReply(previous: ConversationMessage[], event: ProtocolEvent): ConversationMessage[] {
  const text = String(event.text ?? "");
  const duplicate = previous.some((message) => event.rid
    ? message.role === "assistant" && message.rid === event.rid
    : message.role === "assistant" && message.text === text);
  return duplicate ? previous : [
    ...previous,
    { id: messageId("assistant"), role: "assistant", text, rid: event.rid },
  ];
}

export function systemNotice(kind: "error" | "cancelled", event: ProtocolEvent): ConversationMessage {
  return kind === "error"
    ? { id: messageId("error"), role: "system", text: `出错：${String(event.text ?? "未知错误")}`, rid: event.rid }
    : { id: messageId("cancelled"), role: "system", text: "本回合已中断", rid: event.rid };
}
