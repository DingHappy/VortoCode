// 常驻电脑连不上时，助理在本机兜底：这段时间聊的内容只存在本机引擎里，连回常驻电脑后侧栏看不到。
// 这里只记下这些会话的 id；连回去后在「本机（离线时）」列出，内容按需从本机引擎只读读取，
// 由用户决定要不要「带回常驻电脑」（作为文本附件放进输入框）。不合并、不自动同步：
// 两边是各自运行的助理，自动合并会让常驻助理的历史里出现它没说过的话。
import { MAX_TEXT_FILE_BYTES, type Attachment } from "./attachments";
import { STORAGE_KEYS } from "./storage";
import type { PhoneChat } from "../types";

export interface OfflineChatEntry { sid: string; at: string }

const MAX_ENTRIES = 20;

type Store = Pick<Storage, "getItem" | "setItem">;

function read(storage: Store): OfflineChatEntry[] {
  try {
    const raw = JSON.parse(storage.getItem(STORAGE_KEYS.offlineAssistantChats) ?? "[]");
    return Array.isArray(raw)
      ? raw.filter((item): item is OfflineChatEntry => typeof item?.sid === "string" && item.sid !== "" && typeof item?.at === "string")
      : [];
  } catch { return []; }
}

function write(storage: Store, entries: OfflineChatEntry[]): OfflineChatEntry[] {
  try { storage.setItem(STORAGE_KEYS.offlineAssistantChats, JSON.stringify(entries)); } catch { /* 存不下只影响这次以后的列表 */ }
  return entries;
}

export function listOfflineChats(storage: Store = localStorage): OfflineChatEntry[] {
  return read(storage);
}

/** 在本机兜底时发过消息的会话：最新的排前面，同一段只记一次，最多 20 段。 */
export function recordOfflineChat(sid: string, storage: Store = localStorage, at = new Date().toISOString()): OfflineChatEntry[] {
  const rest = read(storage).filter((item) => item.sid !== sid);
  return write(storage, [{ sid, at }, ...rest].slice(0, MAX_ENTRIES));
}

/** 从列表里拿掉（本机的会话记录本身不删）。 */
export function dismissOfflineChat(sid: string, storage: Store = localStorage): OfflineChatEntry[] {
  return write(storage, read(storage).filter((item) => item.sid !== sid));
}

const encoder = new TextEncoder();

/**
 * 把本机这段对话做成文本附件：开头说明来历，正文按「我 / 本机助理」逐条排；
 * 超过文本附件上限时保留最近的部分，并注明前面省略了。
 */
export function offlineChatAttachment(chat: PhoneChat): Attachment {
  const title = (chat.title || "本机对话").replace(/[\\/:*?"<>|\n\r]+/g, " ").trim().slice(0, 30) || "本机对话";
  const header = `以下是常驻电脑连不上时，我在这台 Mac 的本机助理里聊的「${title}」，只作参考资料：`;
  const lines = chat.transcript
    .filter((item) => String(item.text ?? "").trim())
    .map((item) => `${item.role === "user" ? "我" : "本机助理"}：${String(item.text).trim()}`);
  const budget = MAX_TEXT_FILE_BYTES - encoder.encode(header).length - 200;
  const kept: string[] = [];
  let used = 0;
  for (let index = lines.length - 1; index >= 0; index--) {
    const size = encoder.encode(lines[index]).length + 2;
    if (used + size > budget) break;
    kept.unshift(lines[index]);
    used += size;
  }
  const omitted = lines.length - kept.length;
  const data = [header, omitted ? `（前面较早的 ${omitted} 条太长，已省略）` : "", ...kept].filter(Boolean).join("\n\n");
  return { id: `offline-${chat.sid}`, name: `本机对话-${title}.md`, kind: "text", size: encoder.encode(data).length, data };
}
