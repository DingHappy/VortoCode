// 右侧「任务动态」：把后台任务台账投影成一条按时间分组的动态流（参考 Muse）。
// 纯函数、零请求：数据来自已有的 /api/tasks，只决定「显示成什么样」。
import type { AgentTurn, AssistantDispatch, NoticeItem, TaskItem } from "../types";

/** 展示用的五档：等你处理 / 进行中 / 完成 / 失败 / 已停下（暂停、中断，可续跑）。 */
export type FeedStatus = "waiting" | "running" | "done" | "failed" | "stopped";

/** 条目做的是哪类事，决定完成态的图标：联网查资料 / 跑命令 / 改代码文件 / 普通对答 / 后台任务。 */
export type FeedKind = "web" | "command" | "code" | "chat" | "task";

export interface FeedItem {
  id: string;
  kind?: FeedKind;
  /** 对话回合：点开后跳回这一句 */
  rid?: string;
  title: string;
  summary: string;
  status: FeedStatus;
  /** ISO 时间：最近一次更新（没有就用创建时间） */
  at: string;
  session?: string;
  taskId?: string;
  /** 常驻助理的任务单 */
  dispatchId?: string;
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
    kind: "task",
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

const WEB_TOOLS = /^(web_|browser|screenshot)/;
const CODE_TOOLS = /^(write_file|edit_file|apply_patch|dev_)/;

/** 手机（Telegram / 钉钉）上的会话：Desktop 只读查看，不接管。 */
export function isPhoneSession(sid?: string): boolean {
  return /^(sid-)?(telegram|dingtalk)-/.test(sid ?? "");
}

/** 一轮对话 → 一条动态。没有最终回复的回合（中断、出错）记为「已停下」。手机上聊的标 📱。 */
export function turnFeedItem(turn: AgentTurn): FeedItem {
  const names = turn.tool_names ?? [];
  const kind: FeedKind = names.some((name) => WEB_TOOLS.test(name)) ? "web"
    : names.some((name) => CODE_TOOLS.test(name)) ? "code"
    : names.includes("run_command") ? "command" : "chat";
  return {
    id: `turn:${turn.sid}:${turn.rid}`,
    kind,
    rid: turn.rid,
    title: `${isPhoneSession(turn.sid) ? "📱 " : ""}${turn.title || "未命名事项"}`,
    summary: turn.status === "done" ? turn.outcome || "已回复" : isPhoneSession(turn.sid) ? "没有完成，可以在手机上继续" : "没有完成，可以在对话里继续",
    status: turn.status === "done" ? "done" : "stopped",
    at: turn.at,
    session: turn.sid,
  };
}


const DISPATCH_STATUS: Record<AssistantDispatch["status"], FeedStatus> = {
  proposed: "waiting", sending: "running", submission_unknown: "waiting", send_failed: "failed", running: "running", waiting: "waiting",
  done: "done", failed: "failed", cancelled: "stopped", stopped: "stopped",
};

/** 任务单 → 动态：等你确认下发的置顶；下发后跟着常驻电脑上的任务走。撤回的不显示。 */
export function dispatchFeedItem(item: AssistantDispatch): FeedItem | null {
  if (item.status === "cancelled") return null;
  const summary = item.sync_state === "unreachable" ? `${item.project} · 当前无法核实 · 上次核对 ${item.last_checked_at || "尚无记录"}`
    : item.status === "sending" ? `${item.project} · 正在下发，请核对状态`
    : item.status === "submission_unknown" ? `${item.project} · 提交结果待核对`
    : item.status === "proposed" ? `等你确认下发 · ${item.project}${item.tainted ? " · 含外部内容，请细看" : ""}`
    : item.status === "send_failed" ? `下发失败，可以重试：${firstSentence(item.error, MAX_SUMMARY - 10)}`
    : item.status === "waiting" ? `${item.project} · 在等人回答`
    : item.status === "running" ? `${item.project} · 在做`
    : item.status === "done" ? `执行结束，等待审查${item.branch ? ` · ${item.branch}` : ""}`
    : item.status === "stopped" ? `${item.project} · 已停下`
    : firstSentence(item.error, MAX_SUMMARY) || "执行失败";
  return {
    id: `dispatch:${item.id}`,
    kind: "code",
    dispatchId: item.id,
    title: item.title,
    summary,
    status: DISPATCH_STATUS[item.status] ?? "running",
    at: item.updated_at || item.created_at,
  };
}

/**
 * 通知 → 动态：定时作业的结果（⏰）与早晚报（🗒）。其余来源（转发测试、投递告警）不进动态。
 * 它按时间自己做的事也是「它为你做过的事」，和对话、任务单排在一起。
 */
export function noticeFeedItem(notice: NoticeItem, index = 0): FeedItem | null {
  const source = String(notice.source ?? "");
  const text = String(notice.text ?? "").trim();
  const lines = text.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  if (!text || !notice.ts) return null;
  if (source.startsWith("cron:")) {
    const name = source.slice(5) || "定时作业";
    const failed = /^(❌|⚠)/.test(text);
    return {
      id: `notice:${notice.ts}:${index}`, kind: "task", title: `⏰ ${name}`,
      summary: firstSentence(lines.slice(1).join("\n") || lines[0], MAX_SUMMARY) || (failed ? "没有跑成" : "跑完了"),
      status: failed ? "failed" : "done", at: notice.ts,
    };
  }
  if (source === "assistant" && text.startsWith("🗒")) {
    return {
      id: `notice:${notice.ts}:${index}`, kind: "chat", title: firstSentence(lines[0], MAX_TITLE),
      summary: firstSentence(lines.slice(1).join("\n"), MAX_SUMMARY) || "已发到手机", status: "done", at: notice.ts,
    };
  }
  return null;
}
