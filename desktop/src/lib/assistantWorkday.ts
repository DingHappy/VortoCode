import type { AssistantDispatch, CodexAction, CodexProgressSnapshot, PendingConfirmation, PersonalGoal } from "../types";
import { progressLine } from "./assistantTask";
import { readExecutionObservation } from "./executionObservation";
import { hasConfirmedPersonalGoal } from "./personalGoalSnapshot";
import { freshAt } from "./readFreshness";

export interface WorkdaySource<T> {
  status: "loading" | "current" | "unavailable";
  data: T;
  checkedAt?: number;
}
export type WorkdayTarget = { kind: "dispatch" | "goal" | "codex" | "codex_action" | "confirmation"; id: string };
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
const CODEX_REPORT_FRESH_MS = 30 * 60_000;
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
  if (!goalsFresh) result.warnings.push(input.goals.status === "loading" ? "个人目标正在读取。" : "个人目标未能取得最新状态，完成记录暂不计入已核实。");
  for (const goal of input.goals.data) {
    const passed = hasConfirmedPersonalGoal(goal);
    const active = (goal.status !== "achieved" && goal.status !== "failed") || (goal.status === "achieved" && !passed);
    if (!active && !today(goal.updated || goal.created || "", now)) continue;
    const due = goal.overdue || goal.follow_up_due || goal.status === "blocked" || Boolean(goal.blocker);
    const state: WorkdayItem["state"] = !goalsFresh || !["draft", "active", "blocked", "achieved", "failed"].includes(goal.status) ? "unknown" : passed ? "verified"
      : goal.status === "achieved" ? "review" : due || goal.status === "failed" || goal.status === "draft" ? "decision" : "running";
    const detail = !goalsFresh ? "上次记录，当前状态待核对" : passed ? "全部达标条件均有对应的通过记录"
      : goal.status === "achieved" ? "目标标为达成，但验收记录不完整，请核对"
      : goal.blocker || (goal.overdue ? "已过期限：" : goal.follow_up_due ? "到了跟进时间：" : "下一步：") + (goal.next_action || "请补充下一步");
    add({ id: `goal:${goal.id}`, title: goal.objective, detail, state, source: "个人目标",
      at: goal.updated || goal.created || "", target: { kind: "goal", id: goal.id } }, active);
  }
  const codexFresh = input.connected && input.codex.status === "current" && freshAt(input.codex.checkedAt, ms)
    && freshAt(time(input.codex.data?.received_at), ms, CODEX_REPORT_FRESH_MS);
  if (!codexFresh) result.warnings.push(input.codex.status === "loading" ? "Codex 进度正在读取。" : "Codex 尚无新鲜报告，无法确认 Mac 上的当前进展。");
  for (const thread of input.codex.data?.threads ?? []) {
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
    if (!active && !today(updated, now)) continue;
    const waiting = hasExecution ? executionValid && execution.state === "waiting_user"
      : thread.flags.includes("waitingOnApproval") || thread.flags.includes("waitingOnUserInput");
    const state: WorkdayItem["state"] = !codexFresh ? "unknown" : hasExecution
      ? !executionValid ? "unknown" : execution.state === "running" ? "running"
        : execution.state === "finished" ? "review"
        : ["waiting_user", "failed", "cancelled"].includes(execution.state) ? "decision" : "unknown"
      : !["active", "idle"].includes(thread.status) ? "unknown" : waiting ? "decision"
      : active ? thread.turn?.status === "inProgress" ? "running" : "unknown"
      : ["failed", "interrupted"].includes(thread.turn?.status ?? "") ? "decision"
      : thread.turn?.status === "completed" ? "review" : "unknown";
    const detail = !codexFresh ? "报告已过期或连接不可用，请核对 Codex 当前状态"
      : waiting ? "Codex 正在等你批准或回答，请到 Codex 处理"
      : state === "running" ? execution?.progress.summary || thread.turn?.plan || thread.turn?.request || "Codex 报告正在执行"
      : state === "unknown" ? "Codex 状态尚不能确认，请核对原会话"
      : state === "decision" ? thread.turn?.error || "Codex 本轮失败或中断，请决定下一步"
      : "Codex 本轮已结束；最终回复不作为完成验收依据";
    add({ id: `codex:${thread.id}`, title: thread.name || "Codex 会话", detail, state, source: "Codex",
      at: updated || input.codex.data?.received_at || "", target: { kind: "codex", id: thread.id } }, active);
  }
  for (const action of input.codexActions) {
    if (!["proposed", "approved", "sending", "failed"].includes(action.status)) continue;
    if (action.status === "failed" && !today(action.updated_at, now)) continue;
    const unconfirmed = action.status === "approved" || action.status === "sending";
    result.decisions.push({ id: `codex_action:${action.id}`, title: action.thread_name,
      detail: action.status === "approved" ? "动作已批准，尚未确认交给 Codex；请核对原动作，不要重复发送"
        : action.status === "sending" ? "动作已被认领，发送结果仍待核对；请勿重复发送"
        : action.status === "failed" ? action.error || "动作未能确认送达，请核对原因与原会话"
        : action.kind === "interrupt" ? "助理建议停止本轮，等你审阅" : "助理拟好下一步，等你审阅发送正文",
      source: "Codex 动作", state: unconfirmed ? "unknown" : "decision", at: action.updated_at,
      target: { kind: "codex_action", id: action.id } });
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
