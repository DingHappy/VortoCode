import type { AssistantDispatch, AssistantScheduleItem, CodexAction, CodexProgressSnapshot, DesktopRuntimeInbox, MemoryProposal, NoticeItem, OpsHeartbeat, PendingConfirmation, PersonalGoal, SessionSummary } from "../types";
import { progressLine } from "./assistantTask";
import { readExecutionObservation } from "./executionObservation";
import { hasConfirmedPersonalGoal } from "./personalGoalSnapshot";
import { codexActionOutcome } from "./codexActionOutcome";
import { freshAt } from "./readFreshness";
import { buildTaskBoard } from "./taskBoard";
import { CODEX_REPORT_FRESH_MS, codexGoalProgress, codexProgressFresh, goalProgressSummary } from "./codexGoalSupervision";

export interface WorkdaySource<T> {
  status: "loading" | "current" | "unavailable";
  data: T;
  checkedAt?: number;
}
export type WorkdayTarget = { kind: "dispatch" | "goal" | "codex" | "codex_action" | "confirmation" | "schedule" | "memory" | "session" | "project" | "ops" | "notice"; id: string };
export interface WorkdayItem {
  id: string; title: string; detail: string; source: string; at: string;
  state: "running" | "decision" | "review" | "verified" | "unknown";
  target: WorkdayTarget;
}
export type WorkdayBucket = "today" | "progress" | "completed" | "decisions";
export interface AssistantWorkday {
  today: WorkdayItem[]; progress: WorkdayItem[]; completed: WorkdayItem[]; decisions: WorkdayItem[];
  warnings: string[];
}
// Matches the resident Codex watch's STALE_AFTER; report time is shown separately.
const time = (value?: string) => Date.parse(value ?? "");
const today = (value: string, now: Date) => {
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) && parsed.getTime() <= now.getTime()
    && parsed.getFullYear() === now.getFullYear() && parsed.getMonth() === now.getMonth() && parsed.getDate() === now.getDate();
};

/** Read-only projection over existing records; never writes or infers completion from prose. */
export function buildAssistantWorkday(input: {
  dispatches: AssistantDispatch[]; dispatchesLoaded: boolean; codexActions: CodexAction[];
  goals: WorkdaySource<PersonalGoal[]>; codex: WorkdaySource<CodexProgressSnapshot | null>;
  confirmation?: PendingConfirmation | null;
  /** 不传 = 这一版还没接这个来源，不提示；传了就按读取状态说清楚 */
  schedule?: WorkdaySource<AssistantScheduleItem[]>; proposals?: WorkdaySource<MemoryProposal[]>;
  /** 这台运行端的对话列表；activeSid 是当前打开的对话（它的确认卡已由 confirmation 列出），mainSid 用来叫「主要聊天」 */
  sessions?: WorkdaySource<SessionSummary[]>; activeSid?: string; mainSid?: string;
  /** 各项目运行端的工作快照（工作台看板读的那一份）；不传 = 不看项目 */
  projectSources?: DesktopRuntimeInbox[];
  /** 运维报到状态；notices 只用来找 24 小时内「自愈未成功」的记录 */
  ops?: WorkdaySource<OpsHeartbeat[]>; notices?: WorkdaySource<NoticeItem[]>;
  connected: boolean; now: Date;
}): AssistantWorkday {
  const { now } = input, ms = now.getTime();
  const result: AssistantWorkday = { today: [], progress: [], completed: [], decisions: [], warnings: [] };
  const add = (item: WorkdayItem, carry = false) => {
    if (carry || today(item.at, now)) result.today.push(item);
    if (item.state === "running") result.progress.push(item);
    if (item.state === "verified" && today(item.at, now)) result.completed.push(item);
    if (["decision", "review", "unknown"].includes(item.state)) result.decisions.push(item);
  };
  if (!input.connected) result.warnings.push("连接中断，以下为历史记录；恢复连接后才能核实当前进展。");
  if (!input.dispatchesLoaded) result.warnings.push("任务单尚未读取，暂不能确认是否有待办。");
  for (const task of input.dispatches) {
    if (task.status === "cancelled") continue;
    const active = ["proposed", "sending", "submission_unknown", "running", "waiting", "failed", "send_failed", "stopped"].includes(task.status)
      || (task.status === "done" && !task.goal_confirmed_at);
    const relevant = active || today(task.updated_at || task.created_at, now);
    if (!relevant) continue;
    const current = input.connected && input.dispatchesLoaded && task.sync_state === "current"
      && freshAt(time(task.last_checked_at), ms);
    let state: WorkdayItem["state"] = "decision", detail = "";
    if (task.status === "proposed") detail = "任务单已拟好，等你确认下发";
    else if (task.status === "submission_unknown" || task.status === "sending") {
      state = "unknown"; detail = "下发结果待核对，请先查看原任务单";
    } else if (!current) {
      state = "unknown"; detail = "当前状态待核对；上次记录：" + task.status;
    } else if (task.status === "running") {
      state = "running"; detail = progressLine(task) || "项目运行端报告正在执行";
    } else if (task.status === "waiting") detail = task.question?.question || "任务在等你补充信息";
    else if (task.status === "done") {
      state = "review";
      const evidence = task.delivery_evidence;
      const verified = !task.error && evidence?.verification === "passed" && Boolean(evidence.head)
        && evidence.verified_head === evidence.head && freshAt(time(evidence.checked_at), ms);
      detail = verified ? "当前提交验证通过，交付与目标验收仍需核对"
        : "执行已结束，尚无匹配当前提交的有效验证证据";
    } else detail = task.error || (task.status === "stopped" ? "任务已停下，请决定下一步" : "执行失败，请核对原因");
    add({ id: `dispatch:${task.id}`, title: task.title, detail, state, source: task.project,
      at: task.updated_at || task.created_at, target: { kind: "dispatch", id: task.id } }, active);
  }
  const goalsFresh = input.connected && input.goals.status === "current" && freshAt(input.goals.checkedAt, ms);
  const codexFresh = codexProgressFresh(input.codex, input.connected, ms);
  const links = codexGoalProgress({ snapshot: input.codex.data, goals: input.goals.data,
    codexCurrent: codexFresh, goalsCurrent: goalsFresh, now: ms });
  result.warnings.push(...links.warnings);
  if (!goalsFresh) result.warnings.push(input.goals.status === "loading" ? "个人目标正在读取。" : "个人目标未能取得最新状态，完成记录暂不计入已核实。");
  for (const goal of input.goals.data) {
    const passed = hasConfirmedPersonalGoal(goal);
    const active = (goal.status !== "achieved" && goal.status !== "failed") || (goal.status === "achieved" && !passed);
    if (!active && !today(goal.updated || goal.created || "", now)) continue;
    const due = goal.overdue || goal.follow_up_due || goal.status === "blocked" || Boolean(goal.blocker);
    let state: WorkdayItem["state"] = !goalsFresh || !["draft", "active", "blocked", "achieved", "failed"].includes(goal.status) ? "unknown" : passed ? "verified"
      : goal.status === "achieved" ? "review" : due || goal.status === "failed" || goal.status === "draft" ? "decision" : "running";
    let detail = !goalsFresh ? "上次记录，当前状态待核对" : passed ? "全部达标条件均有对应的通过记录"
      : goal.status === "achieved" ? "目标标为达成，但验收记录不完整，请核对"
      : goal.blocker || (goal.overdue ? "已过期限：" : goal.follow_up_due ? "到了跟进时间：" : "下一步：") + (goal.next_action || "请补充下一步");
    const link = links.items.find(item => item.goalId === goal.id);
    if (!passed && goalsFresh && link) {
      if (link.state === "unknown") {
        if (state === "running" || state === "review") state = "unknown";
        detail += "；" + goalProgressSummary(link);
      } else if (["paused", "awaiting_user"].includes(link.state)) {
        state = "decision"; detail = goalProgressSummary(link);
      }
    }
    add({ id: `goal:${goal.id}`, title: goal.objective, detail, state, source: "个人目标",
      at: goal.updated || goal.created || "", target: { kind: "goal", id: goal.id } }, active);
  }
  if (!codexFresh) result.warnings.push(input.codex.status === "loading" ? "Codex 进度正在读取。" : "Codex 尚无新鲜报告，无法确认 Mac 上的当前进展。");
  for (const thread of input.codex.data?.threads ?? []) {
    const link = links.items.find(item => item.threadId === thread.id);
    const ongoingGoal = link !== undefined && link.state !== "confirmed";
    const hasExecution = Object.prototype.hasOwnProperty.call(thread, "execution");
    const execution = hasExecution ? readExecutionObservation(thread.execution) : null;
    const executionValid = execution !== null && execution.ref.backend_id === "codex"
      && execution.ref.session_id === thread.id && execution.ref.turn_id === thread.turn?.id
      && execution.ref.run_id === thread.turn?.id && !execution.ref.task_id
      && freshAt(execution.observed_at * 1000, ms, CODEX_REPORT_FRESH_MS);
    const active = thread.status === "active" || (executionValid && ["running", "waiting_user", "queued"].includes(execution.state));
    if (!active && !thread.turn) continue;
    const updated = typeof thread.updated_at === "number" && Number.isFinite(thread.updated_at)
      && thread.updated_at > 0 && thread.updated_at * 1000 <= ms
      ? new Date(thread.updated_at * 1000).toISOString() : "";
    if (!active && !ongoingGoal && !today(updated, now)) continue;
    const waiting = hasExecution ? executionValid && execution.state === "waiting_user"
      : thread.flags.includes("waitingOnApproval") || thread.flags.includes("waitingOnUserInput");
    let state: WorkdayItem["state"] = !codexFresh ? "unknown" : hasExecution
      ? !executionValid ? "unknown" : execution.state === "running" ? "running"
        : execution.state === "finished" ? "review"
        : ["waiting_user", "failed", "cancelled"].includes(execution.state) ? "decision" : "unknown"
      : !["active", "idle"].includes(thread.status) ? "unknown" : waiting ? "decision"
      : active ? thread.turn?.status === "inProgress" ? "running" : "unknown"
      : ["failed", "interrupted"].includes(thread.turn?.status ?? "") ? "decision"
      : thread.turn?.status === "completed" ? "review" : "unknown";
    let detail = !codexFresh ? "报告已过期或连接不可用，请核对 Codex 当前状态"
      : waiting ? "Codex 正在等你批准或回答，请到 Codex 处理"
      : state === "running" ? execution?.progress.summary || thread.turn?.plan || thread.turn?.request || "Codex 报告正在执行"
      : state === "unknown" ? "Codex 状态尚不能确认，请核对原会话"
      : state === "decision" ? thread.turn?.error || "Codex 本轮失败或中断，请决定下一步"
      : "Codex 本轮已结束；最终回复不作为完成验收依据";
    if (link) {
      if (state === "review" && ["paused", "awaiting_user"].includes(link.state)) state = "decision";
      detail += `；目标「${link.title}」：${link.reason}`;
    }
    add({ id: `codex:${thread.id}`, title: thread.name || "Codex 会话", detail, state, source: "Codex",
      at: updated || input.codex.data?.received_at || "", target: { kind: "codex", id: thread.id } }, active || ongoingGoal);
  }
  for (const action of input.codexActions) {
    if (!["proposed", "approved", "sending", "unknown", "failed"].includes(action.status)) continue;
    if ((action.status === "failed" || action.status === "unknown") && !today(action.updated_at, now)) continue;
    const outcome = codexActionOutcome(action);
    const unconfirmed = outcome === "approved" || outcome === "sending" || outcome === "unknown";
    result.decisions.push({ id: `codex_action:${action.id}`, title: action.thread_name,
      detail: action.status === "approved" ? "动作已批准，尚未确认交给 Codex；请核对原动作，不要重复发送"
        : action.status === "sending" ? "动作已被认领，发送结果仍待核对；请勿重复发送"
        : outcome === "unknown" ? `结果无法确认：${action.error || "交给 Codex 后没收到回执"}；请看 Codex 会话，不要重复发送`
        : outcome === "failed" ? `没发出去：${action.error || "请核对原因与原会话"}`
        : action.kind === "interrupt" ? "助理建议停止本轮，等你审阅" : "助理拟好下一步，等你审阅发送正文",
      source: "Codex 动作", state: unconfirmed ? "unknown" : "decision", at: action.updated_at,
      target: { kind: "codex_action", id: action.id } });
  }
  // 定时任务：还开着、最近一次失败或超时的，到下一次成功之前一直算「等你决定」，不因跨天消失
  if (input.schedule) {
    const fresh = input.connected && input.schedule.status === "current" && freshAt(input.schedule.checkedAt, ms);
    if (!fresh) result.warnings.push(input.schedule.status === "loading" ? "定时任务正在读取。" : "定时任务未能取得最新状态，失败的作业可能没有列出。");
    for (const job of input.schedule.data) {
      if (!job.enabled || (job.last_status !== "failed" && job.last_status !== "timed_out")) continue;
      const what = job.last_status === "timed_out" ? "超时" : "失败";
      const streak = (job.failures ?? 0) > 1 ? `，已连续 ${job.failures} 次` : "";
      add({ id: `schedule:${job.id}`, title: job.title, source: "定时", at: job.last || "",
        state: fresh ? "decision" : "unknown", target: { kind: "schedule", id: job.id },
        detail: fresh ? `最近一次${what}${streak}，下次还会照常运行；请看运行记录决定要不要处理`
          : `上次记录：最近一次${what}，当前状态待核对` }, true);
    }
  }
  // 候选记忆合成一条：你点 ✓ 才会记下，不确认就一直挂着
  if (input.proposals) {
    const fresh = input.connected && input.proposals.status === "current" && freshAt(input.proposals.checkedAt, ms);
    if (!fresh) result.warnings.push(input.proposals.status === "loading" ? "待确认的记忆正在读取。" : "待确认的记忆未能取得最新状态。");
    const pending = input.proposals.data;
    if (pending.length) {
      const latest = pending.map(item => item.created_at).filter(Boolean).sort().pop() ?? "";
      const tainted = pending.filter(item => item.tainted).length;
      add({ id: "memory:proposals", title: `${pending.length} 条记忆等你确认`, source: "记忆", at: latest,
        state: fresh ? "decision" : "unknown", target: { kind: "memory", id: "proposals" },
        detail: !fresh ? "上次记录，当前数量待核对"
          : tainted ? `其中 ${tainted} 条来自读过外部内容的对话，确认前看仔细` : `例如：${pending[0].content}` }, true);
    }
  }
  // 别的对话里卡在确认上的操作：列表只有件数没有正文，点进去才看得到确认卡。
  // 手机（Telegram / 钉钉）对话的确认在手机那头处理，Desktop 对它们只读，不列。
  if (input.sessions) {
    const fresh = input.connected && input.sessions.status === "current" && freshAt(input.sessions.checkedAt, ms);
    if (!fresh) result.warnings.push(input.sessions.status === "loading" ? "对话列表正在读取。"
      : "对话列表未能取得最新状态，其他对话里等你确认的操作可能没有列出。");
    for (const session of input.sessions.data) {
      const count = session.pending_input_count ?? 0;
      if (count <= 0 || /^(telegram|dingtalk)-/.test(session.sid)) continue;
      if (session.sid === input.activeSid && input.confirmation) continue;
      const name = session.sid === input.mainSid ? "主要聊天" : `旁聊「${session.title || "新对话"}」`;
      const updated = Number.isFinite(session.updated) && session.updated > 0 && session.updated * 1000 <= ms
        ? new Date(session.updated * 1000).toISOString() : "";
      add({ id: `session:${session.sid}`, title: `${name}有操作等你确认`, source: "对话", at: updated,
        state: fresh ? "decision" : "unknown", target: { kind: "session", id: session.sid },
        detail: fresh ? `${count} 件，打开对话查看确认卡；不确认它会一直等着` : "上次记录，当前是否仍在等待待核对" }, true);
    }
  }
  // 项目里等你处理的：工作台看板「等你」列里的任务和会话。助理任务单派出去的已在上面列过，不重复；
  // 通用会话（助理自己）由上面几类来源代表。取消的不算；没有生命周期记录的旧任务只把今天结束的当作待验收。
  if (input.projectSources) {
    const dispatched = new Set(input.dispatches.map(task => task.remote_task_id).filter(Boolean));
    const sources = input.projectSources.filter(source => source.scope !== "general");
    for (const source of sources.filter(source => source.snapshot)) {
      const fresh = !source.error && source.snapshot?.version === 1 && freshAt(source.checkedAt, ms);
      if (!fresh) result.warnings.push(`项目「${source.label}」暂时读不到最新状态，那边等你处理的事可能没有列全。`);
    }
    const unread = sources.filter(source => !source.snapshot);
    if (unread.length) result.warnings.push(`还没读到 ${unread.map(source => `「${source.label}」`).join("、")}，那边等你处理的事暂未列出。`);
    for (const item of buildTaskBoard(sources)) {
      if (item.column !== "attention" || (item.kind !== "task" && item.kind !== "session")) continue;
      const task = item.kind === "task" ? item.source.snapshot!.tasks.find(entry => entry.id === item.id) : undefined;
      if (task && (dispatched.has(task.id) || task.status === "cancelled")) continue;
      if (task && !item.lifecycle && task.status === "done" && !today(task.updated, now)) continue;
      const session = item.kind === "session" ? item.source.snapshot!.sessions.find(entry => entry.sid === item.id) : undefined;
      if (session && session.status !== "needs_input" && session.status !== "failed") continue;
      const fresh = !item.source.error && freshAt(item.source.checkedAt, ms);
      const at = task?.updated || (session && session.updated > 0 && session.updated * 1000 <= ms ? new Date(session.updated * 1000).toISOString() : "");
      add({ id: `project:${item.key}`, title: item.title, source: item.source.label, at,
        state: fresh ? (task?.status === "done" ? "review" : "decision") : "unknown",
        target: { kind: "project", id: item.key },
        detail: !fresh ? `上次记录：${item.status}，当前状态待核对`
          : session ? (session.status === "needs_input" ? "这个对话在等你确认或回答" : "这个对话有任务出错或 Hook 报了问题")
          : `${item.status}；到工作台「任务」里查看原任务` }, true);
    }
  }
  // 运维报到：读的是状态不是通知，失败中、超时没报到的一直列着，直到那台机器报到成功
  if (input.ops) {
    const fresh = input.connected && input.ops.status === "current" && freshAt(input.ops.checkedAt, ms);
    if (!fresh) result.warnings.push(input.ops.status === "loading" ? "运维报到正在读取。"
      : "运维报到未能取得最新状态（常驻电脑上的服务可能还没升级），备份等定时活的失败可能没有列出。");
    for (const row of input.ops.data) {
      if (row.status === "ok") continue;
      const sinceOk = time(row.last_ok);
      const hours = Number.isFinite(sinceOk) ? Math.floor((ms - sinceOk) / 3_600_000) : null;
      add({ id: `ops:${row.source}`, title: `运维 · ${row.source}`, source: "运维报到",
        at: row.last_report || row.last_ok || "", state: fresh ? "decision" : "unknown", target: { kind: "ops", id: row.source },
        detail: !fresh ? "上次记录，当前状态待核对"
          : row.status === "failed" ? `最近一次报失败：${row.text || "没有附带原因"}`
          : hours === null ? `从没报到成功（约定每 ${row.expect_hours} 小时内一次）`
          : `已经 ${hours} 小时没有报到成功（约定每 ${row.expect_hours} 小时内一次），可能机器停了或定时任务没跑` }, true);
    }
  }
  // 自愈没成功：只有通知这一份记录，按事件看最近 24 小时
  if (input.notices) {
    const fresh = input.connected && input.notices.status === "current" && freshAt(input.notices.checkedAt, ms);
    if (!fresh) result.warnings.push(input.notices.status === "loading" ? "通知正在读取。" : "通知未能取得最新状态，自愈失败的记录可能没有列出。");
    for (const notice of input.notices.data) {
      const source = String(notice.source ?? "");
      const at = time(notice.ts);
      if (!source.startsWith("self-heal:") || !notice.text.startsWith("⚠") || !Number.isFinite(at) || at > ms || ms - at > 86_400_000) continue;
      add({ id: `notice:${JSON.stringify([notice.ts, source, notice.text])}`, title: "自动修复没有成功", source: "自愈",
        at: notice.ts!, state: fresh ? "decision" : "unknown", target: { kind: "notice", id: JSON.stringify([notice.ts, source, notice.text]) },
        detail: notice.text.replace(/^⚠\s*自愈未成功\s*/, "") || notice.text }, true);
    }
  }
  if (input.confirmation) result.decisions.unshift({ id: `confirmation:${input.confirmation.id}`,
    title: "当前对话有操作等你确认", detail: input.confirmation.tainted
      ? `读过外部内容后提出，请仔细核对：${input.confirmation.text}` : input.confirmation.text,
    source: "当前对话", state: "decision", at: now.toISOString(),
    target: { kind: "confirmation", id: input.confirmation.id } });
  for (const key of ["today", "progress", "completed", "decisions"] as const) {
    result[key] = [...new Map(result[key].map(item => [item.id, item])).values()]
      .sort((a, b) => (time(b.at) || 0) - (time(a.at) || 0));
  }
  return result;
}
