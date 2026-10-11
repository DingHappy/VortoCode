// 右侧「任务动态」：把后台任务台账投影成一条按时间分组的动态流（参考 Muse）。
// 纯函数、零请求：数据来自已有的 /api/tasks，只决定「显示成什么样」。
import type { TaskItem } from "../types";

/** 展示用的五档：等你处理 / 进行中 / 完成 / 失败 / 已停下（暂停、中断，可续跑）。 */
export type FeedStatus = "waiting" | "running" | "done" | "failed" | "stopped";

export interface FeedItem {
  id: string;
  title: string;
  summary: string;
  status: FeedStatus;
  /** ISO 时间：最近一次更新（没有就用创建时间） */
  at: string;
  session?: string;
  taskId?: string;
}

export interface FeedGroup {
  key: string;
  label: string;
  items: FeedItem[];
}

const MAX_TITLE = 32;
const MAX_SUMMARY = 48;

/**
 * 一段文字的第一句：去掉代码块与 Markdown 标记，取第一个非空行，再按句末标点和长度截断。
 * 只去成对的强调符（`**` / `__`）与反引号、行首的 #、>、列表符——单个下划线是标识符的一部分
 * （timeout_scope），不能一并抹掉。链接保留文字。
 */
export function firstSentence(text: string | undefined, max: number): string {
  const line = String(text ?? "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\*\*|__|`/g, "")
    .split(/\n+/)
    .map((part) => part.replace(/^\s*(#{1,6}\s+|>\s*|[-*+]\s+)/, "").trim())
    .find(Boolean) ?? "";
  const sentence = (line.split(/(?<=[。！？!?；;])/)[0] ?? "").trim();
  return sentence.length > max ? `${sentence.slice(0, max - 1)}…` : sentence;
}

function openQuestion(task: TaskItem) {
  return [...(task.collaboration?.questions ?? []), ...(task.development?.questions ?? [])]
    .find((question) => question.status === "open");
}

export function feedStatus(task: TaskItem): FeedStatus {
  if (openQuestion(task) || task.collaboration?.review === "pending") return "waiting";
  if (task.status === "queued" || task.status === "running") return "running";
  if (task.status === "done") return "done";
  if (task.status === "paused" || task.status === "interrupted") return "stopped";
  return "failed";
}

export function taskFeedItem(task: TaskItem): FeedItem {
  const status = feedStatus(task);
  const question = openQuestion(task);
  const summary = question ? `等你回复：${firstSentence(question.question, MAX_SUMMARY - 5)}`
    : status === "waiting" ? "已交付，等你验收"
    : status === "running" ? firstSentence(task.log?.[task.log.length - 1], MAX_SUMMARY) || "正在执行"
    : status === "done" ? firstSentence(task.result, MAX_SUMMARY) || "已完成"
    : status === "stopped" ? (task.status === "paused" ? "已暂停，可以继续" : "意外中断，可以恢复")
    : firstSentence(task.error, MAX_SUMMARY) || (task.status === "cancelled" ? "已取消" : "执行失败");
  return {
    id: `task:${task.id}`,
    taskId: task.id,
    title: firstSentence(task.prompt, MAX_TITLE) || "未命名任务",
    summary,
    status,
    at: task.updated_at || task.created_at || "",
    session: task.owner_session || undefined,
  };
}

const timeOf = (item: FeedItem) => {
  const value = Date.parse(item.at);
  return Number.isFinite(value) ? value : 0;
};
const dayKey = (date: Date) => `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;

/** 「等你处理」永远置顶；其余按时间倒序，分成今天 / 昨天 / 具体日期 / 更早（时间未知）。 */
export function groupFeed(items: FeedItem[], now: Date = new Date()): FeedGroup[] {
  const byTime = [...items].sort((a, b) => timeOf(b) - timeOf(a));
  const groups: FeedGroup[] = [];
  const waiting = byTime.filter((item) => item.status === "waiting");
  if (waiting.length) groups.push({ key: "waiting", label: "等你处理", items: waiting });
  const today = dayKey(now);
  const yesterday = dayKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1));
  for (const item of byTime.filter((entry) => entry.status !== "waiting")) {
    const time = timeOf(item);
    const date = new Date(time);
    const key = time ? dayKey(date) : "unknown";
    const label = !time ? "更早" : key === today ? "今天" : key === yesterday ? "昨天" : `${date.getMonth() + 1}月${date.getDate()}日`;
    const group = groups.find((entry) => entry.key === key);
    if (group) group.items.push(item);
    else groups.push({ key, label, items: [item] });
  }
  return groups;
}

/** 条目右侧的时间：分组标题已经说明了是哪天，这里只给几点几分。 */
export function feedTime(item: FeedItem): string {
  const time = timeOf(item);
  if (!time) return "";
  const date = new Date(time);
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}
