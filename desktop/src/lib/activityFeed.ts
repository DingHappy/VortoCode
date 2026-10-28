// 右侧「任务动态」：把后台任务台账投影成一条按时间分组的动态流（参考 Muse）。
// 纯函数、零请求：复用已有任务、对话回合、任务单和通知，只决定「显示成什么样」。
import type { AgentTurn, AssistantDispatch, CodexAction, NoticeItem, PendingConfirmation, TaskItem } from "../types";
import { progressLine } from "./assistantTask";

/** 展示用的五档：等你处理 / 进行中 / 完成 / 失败 / 已停下（暂停、中断，可续跑）。 */
export type FeedStatus = "waiting" | "running" | "done" | "failed" | "stopped";

/** 条目做的是哪类事，决定完成态的图标：联网查资料 / 跑命令 / 改代码文件 / 普通对答 / 后台任务。 */
export type FeedKind = "web" | "command" | "code" | "chat" | "task";

export interface FeedItem {
  id: string;
  kind?: FeedKind;
  /** 对话回合标识；普通与手机只读对话均可定位原消息。 */
  rid?: string;
  title: string;
  summary: string;
  status: FeedStatus;
  /** 展示语义不等同于验收：已回复、执行结束、简报已生成分别说明。 */
  statusLabel?: string;
  sourceLabel?: string;
  /** 没有可跳转目标的通知，原地展开完整记录。纯文本呈现。 */
  detail?: string;
  /** ISO 时间：最近一次更新（没有就用创建时间） */
  at: string;
  session?: string;
  taskId?: string;
  /** 常驻助理的任务单 */
  dispatchId?: string;
  codexActionId?: string;
  confirmationId?: string;
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
    : status === "done" ? firstSentence(task.result, MAX_SUMMARY) || "执行结束，请查看结果"
    : status === "stopped" ? (task.status === "paused" ? "已暂停，可以继续" : "意外中断，可以恢复")
    : firstSentence(task.error, MAX_SUMMARY) || (task.status === "cancelled" ? "已取消" : "执行失败");
  return {
    id: `task:${task.id}`,
    kind: "task",
    taskId: task.id,
    title: firstSentence(task.prompt, MAX_TITLE) || "未命名任务",
    summary,
    detail: task.result || task.error || summary,
    status,
    sourceLabel: "后台任务",
    statusLabel: status === "done" ? "执行结束" : status === "running" && task.status === "queued" ? "排队中" : undefined,
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

/** 普通分组只给时分；跨天的置顶待处理事项补日期，避免被误认为今天。 */
export function feedTime(item: FeedItem, now: Date = new Date()): string {
  const time = timeOf(item);
  if (!time) return "";
  const date = new Date(time);
  const clock = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  if (item.status !== "waiting" || dayKey(date) === dayKey(now)) return clock;
  return `${date.getFullYear() !== now.getFullYear() ? `${date.getFullYear()}年` : ""}${date.getMonth() + 1}月${date.getDate()}日 ${clock}`;
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
    statusLabel: turn.status === "done" ? "已回复" : "未完成回复",
    sourceLabel: isPhoneSession(turn.sid) ? "手机对话" : "对话",
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
    : item.status === "waiting" ? (item.question ? `在问你：${firstSentence(item.question.question, MAX_SUMMARY - 6)}` : `${item.project} · 在等人回答`)
    : item.status === "running" ? `${item.project} · 在做${progressLine(item) ? ` · ${progressLine(item)}` : ""}`
    : item.status === "done" ? (item.pr_url ? `已开 draft PR，等你在 GitHub 审` : `执行结束，等待审查${item.branch ? ` · ${item.branch}` : ""}`)
    : item.status === "stopped" ? `${item.project} · 已停下`
    : firstSentence(item.error, MAX_SUMMARY) || "执行失败";
  return {
    id: `dispatch:${item.id}`,
    kind: "code",
    dispatchId: item.id,
    title: item.title,
    summary,
    status: DISPATCH_STATUS[item.status] ?? "running",
    sourceLabel: item.project ? `任务单 · ${item.project}` : "助理任务单",
    statusLabel: item.sync_state === "unreachable" ? "状态待核对"
      : item.status === "done" ? "执行结束 · 待审查"
      : item.status === "proposed" ? "待确认下发"
      : item.status === "submission_unknown" ? "提交待核对"
      : item.status === "sending" ? "正在下发" : undefined,
    at: item.updated_at || item.created_at,
  };
}

/** 只投影已有动作；查看记录不会批准、发送或停止 Codex。 */
export function codexActionFeedItem(action: CodexAction): FeedItem | null {
  if (action.status === "cancelled") return null;
  const uncertain = action.status === "approved" || action.status === "sending" || action.status === "unknown";
  return {
    id: `codex_action:${action.id}`, kind: "code", codexActionId: action.id,
    title: `${action.kind === "interrupt" ? "建议叫停" : "拟发消息"}：${action.thread_name}`,
    summary: action.status === "unknown" ? "没收到回执，可能已发出也可能没有；以 Codex 会话为准，不要重复发送"
      : uncertain ? "发送结果尚未核实，请查看原动作；不要重复发送"
      : action.status === "failed" ? firstSentence(action.error, MAX_SUMMARY) || "未能确认送达，请查看原动作"
      : action.status === "expired" ? "动作已经过时，没有发送"
      : action.status === "sent" ? "请求已获受理，不代表 Codex 已完成任务"
      : action.status === "proposed" ? `${firstSentence(action.text, MAX_SUMMARY) || "请查看动作正文"}${action.tainted ? " · 含外部内容，请细看" : ""}`
      : "动作状态待核对，请查看原动作",
    status: action.status === "proposed" || uncertain ? "waiting"
      : action.status === "failed" ? "failed" : action.status === "sent" ? "done" : "stopped",
    statusLabel: action.status === "unknown" ? "结果无法确认" : uncertain ? "发送结果待核对" : action.status === "proposed" ? "待审阅批准"
      : action.status === "sent" ? "请求已受理" : action.status === "expired" ? "已过时"
      : action.status === "failed" ? "未确认送达" : "状态待核对",
    sourceLabel: "Codex 动作", at: action.updated_at || action.created_at,
  };
}

/** 当前确认没有可靠事件时间；不把渲染时刻当作新鲜度证据。 */
export function confirmationFeedItem(confirmation: PendingConfirmation): FeedItem {
  const replying = confirmation.responseState && confirmation.responseState !== "idle";
  return {
    id: `confirmation:${confirmation.id}`, kind: "task", confirmationId: confirmation.id,
    title: "当前对话有操作需要确认", at: "", status: "waiting", sourceLabel: "当前对话",
    statusLabel: replying ? "确认回执待核对" : "待你确认",
    summary: replying ? "请查看原确认卡核对回执，不会自动重发批准"
      : `${confirmation.tainted ? "含外部内容，请细看：" : ""}${firstSentence(confirmation.text, MAX_SUMMARY)}`,
  };
}

/**
 * 通知 → 动态：定时作业的结果（⏰）与早晚报（🗒）。其余来源（转发测试、投递告警）不进动态。
 * 它按时间自己做的事也是「它为你做过的事」，和对话、任务单排在一起。
 */
export function noticeFeedItem(notice: NoticeItem): FeedItem | null {
  const source = String(notice.source ?? "");
  const text = String(notice.text ?? "").trim();
  const lines = text.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  if (!text || !notice.ts) return null;
  // The legacy notice ledger has no ID. Use its full tuple instead of array position;
  // inserting another notice must not remount the record the user is reading.
  const id = `notice:${JSON.stringify([notice.ts, source, text])}`;
  if (source.startsWith("cron:")) {
    const name = source.slice(5) || "定时作业";
    const failed = /^(❌|⚠)/.test(text);
    return {
      id, kind: "task", title: `⏰ ${name}`,
      summary: firstSentence(lines.slice(1).join("\n") || lines[0], MAX_SUMMARY) || (failed ? "没有跑成" : "跑完了"),
      status: failed ? "failed" : "done", at: notice.ts,
      sourceLabel: "定时任务", statusLabel: failed ? "执行失败" : "执行结束", detail: text,
    };
  }
  // 家里其它机器的运维报到（🔴 报失败 / 🔺 超时没报到 / ✅ 已恢复）和自动修复的结果：原来只推手机，动态里看不到
  if (source.startsWith("ops:") || source.startsWith("self-heal:")) {
    const ops = source.startsWith("ops:");
    const name = source.slice(source.indexOf(":") + 1) || (ops ? "运维" : "自愈");
    const failed = /^(🔴|🔺|⚠)/.test(text);
    const recovered = /^(✅|🔧)/.test(text);
    return {
      id, kind: "task", title: `${ops ? "运维" : "自愈"} · ${name}`,
      summary: firstSentence(text.replace(/^(🔴|🔺|⚠|✅|🔧)\s*/u, ""), MAX_SUMMARY), at: notice.ts,
      status: failed ? "failed" : "done", sourceLabel: ops ? "运维报到" : "自动修复",
      statusLabel: failed ? (text.startsWith("🔺") ? "没报到" : ops ? "报失败" : "未成功") : recovered ? (ops ? "已恢复" : "已修复") : "消息",
      detail: text,
    };
  }
  if (source === "assistant" && text.startsWith("🗒")) {
    return {
      id, kind: "chat", title: firstSentence(lines[0], MAX_TITLE),
      summary: firstSentence(lines.slice(1).join("\n"), MAX_SUMMARY) || "已发到手机", status: "done", at: notice.ts,
      sourceLabel: "助理简报", statusLabel: "简报已生成", detail: text,
    };
  }
  return null;
}

/** Identical legacy notices still need unique UI keys; ordinal only within an identical tuple. */
export function noticeFeedItems(notices: NoticeItem[]): FeedItem[] {
  const occurrences = new Map<string, number>();
  return notices.flatMap(notice => {
    const item = noticeFeedItem(notice);
    if (!item) return [];
    const occurrence = occurrences.get(item.id) ?? 0;
    occurrences.set(item.id, occurrence + 1);
    return [{ ...item, id: `${item.id}:${occurrence}` }];
  });
}

/** 助理「动态」：回合、派活单与通知、后台任务、Codex 动作，以及正等你批的那条确认。顺序即拼接顺序。 */
export function assistantFeed({ turns, dispatches, notices, taskItems, codexActions, confirmation }: {
  turns: AgentTurn[];
  dispatches: AssistantDispatch[];
  notices: NoticeItem[];
  /** 已映射好的后台任务（工作台「任务动态」也用同一份） */
  taskItems: FeedItem[];
  codexActions: CodexAction[];
  confirmation: PendingConfirmation | null;
}): FeedItem[] {
  const dispatchItems = [...dispatches.map(dispatchFeedItem), ...noticeFeedItems(notices)]
    .filter((item): item is FeedItem => item !== null);
  return [...turns.map(turnFeedItem), ...dispatchItems, ...taskItems,
    ...codexActions.map(codexActionFeedItem).filter((item): item is FeedItem => item !== null),
    ...(confirmation ? [confirmationFeedItem(confirmation)] : [])];
}
