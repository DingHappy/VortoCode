import { expect, it } from "vitest";
import type { AssistantDispatch, AssistantScheduleItem, CodexAction, CodexProgressSnapshot, DesktopRuntimeInbox, MemoryProposal, OpsHeartbeat, PendingConfirmation, PersonalGoal, RuntimeInboxSession, RuntimeInboxTask, SessionSummary } from "../types";
import { buildAssistantWorkday } from "./assistantWorkday";
import executionWire from "../../../tests/fixtures/execution_observation_v1.json";
import { vi } from "vitest";
import { assistantWorkdayReads, startWorkdayClock } from "./assistantWorkdayReads";
import { goalProgressFixture } from "./codexGoalSupervision.fixtures";

function linkedInput() {
  const data = input(), fixture = goalProgressFixture(now.getTime());
  data.goals.data = [fixture.goal]; data.codex.data = fixture.snapshot;
  const link = (fixture.snapshot.goal_supervision as Array<Record<string, unknown>>)[0];
  return { data, ...fixture, link };
}

it("puts a goal awaiting user acceptance into decisions using its original target", () => {
  const { data, goal } = linkedInput(), result = buildAssistantWorkday(data);
  const item = result.today.find(i => i.id === `goal:${goal.id}`)!;
  expect(item).toMatchObject({ state: "decision", target: { kind: "goal", id: goal.id } });
  expect(item.detail).toContain("本轮已结束"); expect(item.detail).toContain("已判断 1 / 4 次");
  expect(result.completed).toHaveLength(0);
  expect(result.today.filter(i => i.target.kind === "goal")).toHaveLength(1);
});
it("does not claim Codex stopped merely because goal supervision paused", () => {
  const { data, snapshot, link } = linkedInput();
  link.state = "paused"; link.reason = "相同缺口连续两轮没有新证据";
  snapshot.threads[0].status = "active"; snapshot.threads[0].turn!.status = "inProgress";
  (snapshot.threads[0].execution as { state: string }).state = "running";
  const result = buildAssistantWorkday(data);
  expect(result.today.find(i => i.target.kind === "goal")!.state).toBe("decision");
  expect(result.progress.find(i => i.target.kind === "codex")!.detail).toContain("相同缺口");
});
it("keeps an associated unfinished goal and idle Codex visible across days", () => {
  const { data, snapshot, goal } = linkedInput();
  goal.updated = yesterday; snapshot.threads[0].updated_at = Date.parse(yesterday) / 1000;
  const result = buildAssistantWorkday(data);
  expect(result.decisions.map(i => i.target.kind)).toEqual(["goal", "codex"]);
});
it.each(["disconnect", "stale report", "goal version"])("does not reuse an old waiting reason as current: %s", fault => {
  const { data, snapshot, goal } = linkedInput();
  if (fault === "disconnect") data.connected = false;
  if (fault === "stale report") snapshot.received_at = yesterday;
  if (fault === "goal version") goal.revision++;
  const item = buildAssistantWorkday(data).today.find(i => i.target.kind === "goal")!;
  expect(item.state).toBe("unknown"); expect(item.detail).not.toContain("本轮已结束");
});
it("never demotes independently confirmed goal evidence based on an unavailable Codex report", () => {
  const { data, goal } = linkedInput();
  goal.status = "achieved";
  goal.acceptance_criteria.forEach((c, i) => {
    c.status = "passed"; c.evidence_ids = [`e-${i}`];
    goal.evidence.push({ id: `e-${i}`, criterion_id: c.id, kind: "personal_acceptance", source: "user_confirmation", summary: "用户已核对", passed: true });
  });
  data.codex.status = "unavailable" as typeof data.codex.status;
  expect(buildAssistantWorkday(data).completed.map(i => i.id)).toEqual([`goal:${goal.id}`]);
});

const now = new Date(2026, 9, 23, 12), at = now.toISOString();
const yesterday = new Date(2026, 9, 22, 12).toISOString();
function dispatch(changes: Partial<AssistantDispatch> = {}): AssistantDispatch {
  return { id: "d1", project: "Project", title: "Fix login", goal: "fix", criteria: ["tests"], status: "running",
    tainted: false, remote_task_id: "t1", result: "", error: "", branch: "vorto/task", created_at: yesterday,
    updated_at: at, sync_state: "current", last_checked_at: at, ...changes };
}
function goal(changes: Partial<PersonalGoal> = {}): PersonalGoal {
  return { id: "g1", kind: "personal", revision: 1, objective: "Ship release", status: "achieved",
    constraints: [], non_goals: [], task_ids: [], due_at: "", follow_up_at: "", overdue: false, follow_up_due: false,
    created: yesterday, updated: at, next_action: "Review delivery", progress: { total: 1, passed: 1, failed: 0 },
    acceptance_criteria: [{ id: "c1", text: "Checked", status: "passed", evidence_ids: ["e1"] }],
    evidence: [{ id: "e1", criterion_id: "c1", kind: "personal_acceptance", source: "user_confirmation", summary: "Reviewed output", passed: true }], ...changes };
}
function codex(changes: Partial<CodexProgressSnapshot> = {}): CodexProgressSnapshot {
  return { overview: "summary", received_at: at, threads: [{ id: "th1", name: "Codex login", status: "active", flags: [],
    updated_at: now.getTime() / 1000, turn: { id: "turn1", status: "inProgress", request: "Fix login" } }], ...changes };
}
function input() {
  return { dispatches: [] as AssistantDispatch[], dispatchesLoaded: true, codexActions: [] as CodexAction[], connected: true, now,
    goals: { status: "current" as const, checkedAt: now.getTime(), data: [] as PersonalGoal[] },
    codex: { status: "current" as const, checkedAt: now.getTime(), data: codex({ threads: [] }) as CodexProgressSnapshot | null } };
}

it("includes unfinished work and unreviewed deliveries from earlier days", () => {
  const data = input();
  data.dispatches = [dispatch({ id: "running", updated_at: yesterday }),
    dispatch({ id: "review", status: "done", updated_at: yesterday }),
    dispatch({ id: "failed", status: "failed", updated_at: yesterday }),
    dispatch({ id: "cancelled", status: "cancelled" }),
    dispatch({ id: "accepted", status: "done", updated_at: yesterday, goal_confirmed_at: yesterday })];
  const result = buildAssistantWorkday(data);
  expect(result.today.map(item => item.id)).toEqual(["dispatch:running", "dispatch:review", "dispatch:failed"]);
  expect(result.progress).toHaveLength(1); expect(result.decisions).toHaveLength(2);
});

it("keeps previously stopped tasks and goals with incomplete acceptance visible across days", () => {
  const data = input();
  data.dispatches = [dispatch({ status: "stopped", updated_at: yesterday })];
  data.goals.data = [goal({ updated: yesterday, evidence: [] })];
  const result = buildAssistantWorkday(data);
  expect(result.today.map(item => item.id)).toEqual(["dispatch:d1", "goal:g1"]);
  expect(result.decisions.map(item => item.state)).toEqual(["decision", "review"]);
  expect(result.completed).toHaveLength(0);
});

it.each(["blank goal", "blank criterion", "empty ids", "missing evidence id", "duplicate criterion", "duplicate evidence"])(
  "cannot invent acceptance from ambiguous or missing identifiers: %s", fault => {
    const item = goal();
    if (fault === "blank goal") item.id = "";
    if (fault === "blank criterion") item.acceptance_criteria[0].id = "";
    if (["empty ids", "missing evidence id"].includes(fault)) item.acceptance_criteria[0].evidence_ids = [];
    if (fault === "missing evidence id") delete (item.evidence[0] as Partial<typeof item.evidence[0]>).id;
    if (fault === "duplicate criterion") item.acceptance_criteria.push({ ...item.acceptance_criteria[0] });
    if (fault === "duplicate evidence") item.evidence.push({ ...item.evidence[0] });
    const data = input(); data.goals.data = [item];
    const result = buildAssistantWorkday(data);
    expect(result.completed).toHaveLength(0);
    expect(result.decisions[0].state).toBe("review");
  });

it.each(["approved", "sending", "failed"] as const)("keeps an unresolved Codex action visible: %s", status => {
  const data = input();
  data.codexActions = [{ id: "a1", status, kind: "message", thread_name: "Review", error: "", updated_at: status === "failed" ? at : yesterday } as CodexAction];
  const result = buildAssistantWorkday(data);
  expect(result.decisions[0].target).toEqual({ kind: "codex_action", id: "a1" });
  expect(result.decisions[0].state).toBe(status === "failed" ? "decision" : "unknown");
  expect(result.completed).toHaveLength(0);
});

it("keeps terminal action failures in history rather than carrying them indefinitely", () => {
  const data = input();
  data.codexActions = [{ id: "a1", status: "failed", updated_at: yesterday } as CodexAction];
  expect(buildAssistantWorkday(data).decisions).toHaveLength(0);
});

it("shows an unknown Codex outcome as unconfirmed today, without carrying it forever", () => {
  const data = input();
  data.codexActions = [{ id: "a1", status: "unknown", kind: "message", thread_name: "Review", error: "", updated_at: at } as CodexAction];
  const [decision] = buildAssistantWorkday(data).decisions;
  expect(decision).toMatchObject({ state: "unknown", target: { kind: "codex_action", id: "a1" } });
  expect(decision.detail).toContain("不要重复发送");
  data.codexActions = [{ ...data.codexActions[0], updated_at: yesterday }];
  expect(buildAssistantWorkday(data).decisions).toHaveLength(0);
});

class Visibility extends EventTarget { hidden = false; }
it("expires completion evidence while network refresh is still pending", async () => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  const data = input(); data.goals.data = [goal()];
  let model = buildAssistantWorkday(data);
  let resolve!: (value: PersonalGoal[]) => void;
  const client = { listPersonalGoals: () => new Promise<PersonalGoal[]>(done => { resolve = done; }),
    getCodexProgress: async () => ({ overview: "", threads: [] }) };
  const reads = assistantWorkdayReads({ client: () => client, current: () => true,
    goals: source => Object.assign(data.goals, source), codex: () => {} });
  const pending = reads.refresh();
  const stop = startWorkdayClock(date => { model = buildAssistantWorkday({ ...data, now: date }); }, new Visibility());
  try {
    expect(model.completed).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(121_000);
    expect(model.completed).toHaveLength(0);
    expect(model.today[0].state).toBe("unknown");
  } finally { stop(); resolve([]); await pending; vi.useRealTimers(); }
});

it("rolls the completed-day bucket at midnight without requiring an HTTP response", async () => {
  vi.useFakeTimers();
  const before = new Date(2026, 9, 23, 23, 59, 59);
  vi.setSystemTime(before);
  const data = input(); data.goals.data = [goal()]; data.goals.checkedAt = before.getTime();
  let model = buildAssistantWorkday({ ...data, now: before });
  const stop = startWorkdayClock(date => { model = buildAssistantWorkday({ ...data, now: date }); }, new Visibility());
  try {
    expect(model.completed).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(model.completed).toHaveLength(0);
  } finally { stop(); vi.useRealTimers(); }
});

it("finished tasks, successful prose, model acceptance and PR links never become confirmed goals", () => {
  const data = input();
  data.dispatches = [dispatch({ status: "done", result: "Everything passed", pr_url: "https://example.test/pr/1",
    outcome: { kind: "acceptance", criteria: [{ text: "tests", verdict: "met", evidence: "model said done" }] } })];
  const result = buildAssistantWorkday(data);
  expect(result.completed).toEqual([]);
  expect(result.decisions[0].detail).toContain("尚无匹配当前提交");
});

it.each(["match", "wrong head", "stale", "error"])("commit verification is accurately described (%s) and still requires review", kind => {
  const data = input();
  data.dispatches = [dispatch({ status: "done", error: kind === "error" ? "failed" : "", delivery_evidence: {
    head: "abc", verified_head: kind === "wrong head" ? "old" : "abc", verification: kind === "stale" ? "stale" : "passed",
    verified_at: at, checked_at: at, coverage_known: false, accepted_hunks: null, total_hunks: null,
  } })];
  const result = buildAssistantWorkday(data);
  expect(result.completed).toHaveLength(0);
  expect(result.decisions[0].detail.includes("当前提交验证通过")).toBe(kind === "match");
});

it("counts a personal goal only when every criterion has matching latest acceptance evidence", () => {
  const data = input(); data.goals.data = [goal()];
  const result = buildAssistantWorkday(data);
  expect(result.completed.map(item => item.id)).toEqual(["goal:g1"]);
  expect(result.completed[0].detail).toContain("全部达标条件");
});

it.each(["missing", "wrong criterion", "stale", "failed latest", "empty", "model", "wrong source"])("does not promote incomplete acceptance evidence: %s", fault => {
  const item = goal();
  if (fault === "missing") item.evidence = [];
  else if (fault === "wrong criterion") item.evidence[0].criterion_id = "other";
  else if (fault === "stale") item.evidence[0].stale_reason = "changed";
  else if (fault === "failed latest") {
    item.evidence.push({ ...item.evidence[0], id: "e2", passed: false });
    item.acceptance_criteria[0].evidence_ids.push("e2");
  } else if (fault === "empty") item.evidence[0].summary = " ";
  else if (fault === "model") item.evidence[0].kind = "model";
  else item.evidence[0].source = "model";
  const data = input(); data.goals.data = [item];
  const result = buildAssistantWorkday(data);
  expect(result.completed).toHaveLength(0); expect(result.decisions[0].state).toBe("review");
});

it.each(["offline", "failed source", "old source", "future source"])("retains historical goals without claiming current completion: %s", fault => {
  const data = input(); data.goals.data = [goal()];
  if (fault === "offline") data.connected = false;
  else if (fault === "failed source") Object.assign(data.goals, { status: "unavailable" });
  else data.goals.checkedAt = now.getTime() + (fault === "future source" ? 1_000 : -180_000);
  const result = buildAssistantWorkday(data);
  expect(result.completed).toHaveLength(0); expect(result.today[0].state).toBe("unknown");
  expect(result.warnings.length).toBeGreaterThan(0);
});

it("completion uses the local calendar day and excludes yesterday and future records", () => {
  const data = input();
  data.goals.data = [goal({ id: "today", updated: new Date(2026, 9, 23, 0, 1).toISOString() }),
    goal({ id: "yesterday", updated: new Date(2026, 9, 22, 23, 59).toISOString() }),
    goal({ id: "future", updated: new Date(2026, 9, 24, 0, 1).toISOString() })];
  expect(buildAssistantWorkday(data).completed.map(item => item.id)).toEqual(["goal:today"]);
});

it("draft or overdue goals require a decision instead of looking like an executing agent", () => {
  const data = input(); data.goals.data = [goal({ id: "draft", status: "draft" }),
    goal({ id: "late", status: "active", overdue: true })];
  const result = buildAssistantWorkday(data);
  expect(result.progress).toHaveLength(0); expect(result.decisions).toHaveLength(2);
});

it("a fresh observation heartbeat does not make old idle Codex work a task for today", () => {
  const data = input();
  data.codex.data = codex(); data.codex.data.threads[0].updated_at = Date.parse(yesterday) / 1000;
  expect(buildAssistantWorkday(data).progress).toHaveLength(1);
  data.codex.data.threads[0].status = "idle"; data.codex.data.threads[0].turn!.status = "completed";
  expect(buildAssistantWorkday(data).today).toHaveLength(0);
});

it("Codex final responses never substitute for acceptance", () => {
  const data = input(); data.codex.data = codex();
  data.codex.data.threads[0].status = "idle";
  data.codex.data.threads[0].turn = { id: "t", status: "completed", reply: "All done and deployed" };
  const result = buildAssistantWorkday(data);
  expect(result.completed).toHaveLength(0); expect(result.decisions[0].detail).toContain("不作为完成验收依据");
});

it.each(["expired", "missing", "unavailable"])("stale Codex observations remain unknown: %s", fault => {
  const data = input(); data.codex.data = codex();
  if (fault === "expired") data.codex.data.received_at = yesterday;
  else if (fault === "missing") delete data.codex.data.received_at;
  else Object.assign(data.codex, { status: "unavailable" });
  const result = buildAssistantWorkday(data);
  expect(result.progress).toHaveLength(0); expect(result.today[0].state).toBe("unknown");
});

it("native Codex questions and proposed messages are decisions, sent messages are not completion", () => {
  const data = input(); data.codex.data = codex();
  data.codex.data.threads[0].flags = ["waitingOnApproval"];
  const action = { id: "a1", thread_name: "Codex login", kind: "message", status: "proposed", updated_at: at } as CodexAction;
  data.codexActions = [action, { ...action, id: "a2", status: "sent" }];
  const result = buildAssistantWorkday(data);
  expect(result.decisions).toHaveLength(2); expect(result.completed).toHaveLength(0);
  expect(result.decisions[0].detail).toContain("到 Codex 处理");
});

it("unknown sources warn without claiming an empty day is complete", () => {
  const data = input(); data.dispatchesLoaded = false;
  data.codex.data = null; Object.assign(data.goals, { status: "unavailable" });
  expect(buildAssistantWorkday(data).warnings).toHaveLength(3);
});

it("idle Codex conversations with no work are not tasks for today", () => {
  const data = input(); data.codex.data = codex();
  data.codex.data.threads[0].status = "idle"; data.codex.data.threads[0].turn = null;
  expect(buildAssistantWorkday(data).today).toHaveLength(0);
});

it("current confirmation is an explicit decision target and retains external-content warning", () => {
  const result = buildAssistantWorkday({ ...input(), confirmation: { id: "ask", text: "Publish report", tainted: true } });
  expect(result.decisions[0].target).toEqual({ kind: "confirmation", id: "ask" });
  expect(result.decisions[0].detail).toContain("读过外部内容");
});

function commonCodex(state = "running") {
  const data = codex();
  data.threads[0].execution = { ...executionWire, state, observed_at: now.getTime() / 1000,
    ref: { ...executionWire.ref, session_id: "th1", turn_id: "turn1", run_id: "turn1" } };
  return data;
}

it.each([ ["running", "running"], ["waiting_user", "decision"], ["finished", "review"],
  ["failed", "decision"], ["cancelled", "decision"], ["queued", "unknown"],
  ["unknown", "unknown"], ["vendor_future_state", "unknown"],
])("projects common execution state %s without claiming business completion", (state, expected) => {
  const data = input(); data.codex.data = commonCodex(state);
  const result = buildAssistantWorkday(data);
  expect(result.today[0].state).toBe(expected);
  expect(result.completed).toHaveLength(0);
  if (state === "waiting_user") expect(result.decisions[0].detail).toContain("到 Codex 处理");
  if (state === "running") expect(result.progress[0].detail).toBe("Review current changes");
});

it.each(["null", "version", "malformed", "backend", "session", "turn", "run", "task", "expired", "future"])(
  "unusable common observations never fall back to legacy running fields: %s", fault => {
    const data = input(); data.codex.data = commonCodex();
    const thread = data.codex.data.threads[0];
    const wire = thread.execution as typeof executionWire;
    if (fault === "null") thread.execution = null;
    else if (fault === "version") wire.schema_version = 2;
    else if (fault === "malformed") thread.execution = {};
    else if (["backend", "session", "turn", "run", "task"].includes(fault)) {
      const field = { backend: "backend_id", session: "session_id", turn: "turn_id", run: "run_id", task: "task_id" }[fault]!;
      (wire.ref as Record<string, string>)[field] = "elsewhere";
    } else wire.observed_at += fault === "future" ? 1000 : -31 * 60;
    const result = buildAssistantWorkday(data);
    expect(result.progress).toHaveLength(0); expect(result.completed).toHaveLength(0);
    expect(result.today[0].state).toBe("unknown");
  });

it("enabled scheduled jobs whose last run failed or timed out need a decision until a run succeeds, across days", () => {
  const job = (id: string, changes: Partial<AssistantScheduleItem> = {}): AssistantScheduleItem =>
    ({ id, kind: "cron", title: id, schedule: "0 9 * * *", enabled: true, last: yesterday, last_status: "failed", ...changes });
  const model = buildAssistantWorkday({ ...input(), schedule: { status: "current", checkedAt: now.getTime(), data: [
    job("早报", { failures: 3 }), job("巡检", { last_status: "timed_out" }), job("ok", { last_status: "done" }),
    job("off", { enabled: false }), job("running", { last_status: "running" }),
  ] } });
  expect(model.decisions.map(item => item.title)).toEqual(["早报", "巡检"]);
  expect(model.today.map(item => item.title)).toEqual(["早报", "巡检"]);       // 昨天失败的今天照样列着
  expect(model.decisions[0].detail).toContain("最近一次失败，已连续 3 次");
  expect(model.decisions[1].detail).toContain("最近一次超时");
  expect(model.decisions[0].target).toEqual({ kind: "schedule", id: "早报" });
  expect(model.warnings).toEqual([]);
});

it("an unread or stale schedule is reported, and old failures stay as unknown instead of disappearing", () => {
  const failed: AssistantScheduleItem = { id: "j", kind: "report", title: "早报", schedule: "", enabled: true, last_status: "failed", last: at };
  const stale = buildAssistantWorkday({ ...input(), schedule: { status: "unavailable", checkedAt: now.getTime() - 600_000, data: [failed] } });
  expect(stale.warnings).toContain("定时任务未能取得最新状态，失败的作业可能没有列出。");
  expect(stale.decisions[0]).toMatchObject({ state: "unknown", detail: "上次记录：最近一次失败，当前状态待核对" });
  expect(buildAssistantWorkday({ ...input(), schedule: { status: "loading", data: [] } }).warnings).toContain("定时任务正在读取。");
});

it("pending memory proposals become one decision, flagging those from turns that read outside content", () => {
  const proposal = (id: string, tainted = false): MemoryProposal => ({ id, content: `内容 ${id}`, created_at: at, tainted });
  const model = buildAssistantWorkday({ ...input(), proposals: { status: "current", checkedAt: now.getTime(),
    data: [proposal("a"), proposal("b", true)] } });
  expect(model.decisions).toHaveLength(1);
  expect(model.decisions[0]).toMatchObject({ title: "2 条记忆等你确认", source: "记忆", state: "decision",
    target: { kind: "memory", id: "proposals" } });
  expect(model.decisions[0].detail).toContain("其中 1 条来自读过外部内容的对话");
  const none = buildAssistantWorkday({ ...input(), proposals: { status: "current", checkedAt: now.getTime(), data: [] } });
  expect(none.decisions).toEqual([]); expect(none.warnings).toEqual([]);
  expect(buildAssistantWorkday({ ...input(), proposals: { status: "unavailable", data: [] } }).warnings)
    .toContain("待确认的记忆未能取得最新状态。");
});

it("sources the caller does not wire in are neither listed nor warned about", () => {
  expect(buildAssistantWorkday(input()).warnings).toEqual([]);
});

it("other conversations stuck on a confirmation are listed; the current one and phone chats are not", () => {
  const session = (sid: string, pending: number, title = sid): SessionSummary =>
    ({ sid, title, messages: 3, updated: now.getTime() / 1000 - 60, pending_input_count: pending, status: pending ? "needs_input" : "idle" });
  const confirmation = { id: "c1", text: "写文件", tainted: false } as PendingConfirmation;
  const model = buildAssistantWorkday({ ...input(), confirmation, activeSid: "current", mainSid: "main",
    sessions: { status: "current", checkedAt: now.getTime(), data: [
      session("main", 1), session("side", 2, "整理周报"), session("current", 1), session("idle", 0), session("telegram-123", 1),
    ] } });
  const titles = model.decisions.map(item => item.title);
  expect(titles).toContain("主要聊天有操作等你确认");
  expect(titles).toContain("旁聊「整理周报」有操作等你确认");
  expect(model.decisions.filter(item => item.id.startsWith("session:")).map(item => item.id)).toEqual(expect.arrayContaining(["session:main", "session:side"]));
  expect(model.decisions.filter(item => item.id.startsWith("session:"))).toHaveLength(2);   // 当前对话由确认卡那条代表
  expect(model.decisions.find(item => item.id === "session:side")).toMatchObject({
    detail: "2 件，打开对话查看确认卡；不确认它会一直等着", target: { kind: "session", id: "side" }, state: "decision" });
  expect(model.today.map(item => item.id)).toContain("session:main");
  // 当前对话的确认卡还没读到时，靠列表兜底，不漏掉
  const fallback = buildAssistantWorkday({ ...input(), activeSid: "current",
    sessions: { status: "current", checkedAt: now.getTime(), data: [session("current", 1)] } });
  expect(fallback.decisions.map(item => item.id)).toEqual(["session:current"]);
});

it("an unread or stale session list is reported instead of implying nothing is waiting", () => {
  const stale = buildAssistantWorkday({ ...input(), sessions: { status: "unavailable", checkedAt: now.getTime() - 600_000,
    data: [{ sid: "s", title: "x", messages: 1, updated: 0, pending_input_count: 1 }] } });
  expect(stale.warnings).toContain("对话列表未能取得最新状态，其他对话里等你确认的操作可能没有列出。");
  expect(stale.decisions[0]).toMatchObject({ state: "unknown", detail: "上次记录，当前是否仍在等待待核对", at: "" });
});

function projectSource(changes: Partial<DesktopRuntimeInbox> = {}, tasks: RuntimeInboxTask[] = [], sessions: RuntimeInboxSession[] = []): DesktopRuntimeInbox {
  return { runtimeId: "remote:p1", projectId: "p1", scope: "project", label: "示例项目", baseUrl: "", error: "", checkedAt: now.getTime(),
    snapshot: { version: 1, scope: "project", generated_at: at, counts: {} as never, goals: [], decisions: [], sessions, tasks }, ...changes };
}
const projectTask = (id: string, changes: Partial<RuntimeInboxTask> = {}): RuntimeInboxTask =>
  ({ id, status: "failed", prompt: id, detail: "", owner_session: "sid-s1", goal_id: "", plan_id: "", branch: "", updated: at, ...changes });

it("project work waiting on you is listed once, without assistant dispatches, cancellations or old legacy completions", () => {
  const session = { sid: "s9", title: "迁移脚本", status: "needs_input", updated: now.getTime() / 1000 - 30 } as RuntimeInboxSession;
  const model = buildAssistantWorkday({ ...input(),
    dispatches: [dispatch({ remote_task_id: "via-dispatch", status: "running" })],
    projectSources: [
      projectSource({}, [
        projectTask("broken"), projectTask("finished-today", { status: "done" }),
        projectTask("finished-last-week", { status: "done", updated: yesterday }), projectTask("cancelled", { status: "cancelled" }),
        projectTask("via-dispatch"), projectTask("running", { status: "running" }),
      ], [session]),
      projectSource({ runtimeId: "general", scope: "general", label: "通用会话" }, [projectTask("assistant-own")]),
    ] });
  const project = model.decisions.filter(item => item.target.kind === "project");
  expect(project.map(item => item.title).sort()).toEqual(["broken", "finished-today", "迁移脚本"]);
  expect(project.every(item => item.source === "示例项目")).toBe(true);
  expect(project.find(item => item.title === "finished-today")?.state).toBe("review");
  expect(project.find(item => item.title === "迁移脚本")?.detail).toBe("这个对话在等你确认或回答");
  expect(model.warnings).toEqual([]);
});

it("an unreachable or unread project is reported, and its old items become unknown", () => {
  const stale = buildAssistantWorkday({ ...input(), projectSources: [
    projectSource({ error: "offline", checkedAt: now.getTime() - 600_000 }, [projectTask("broken")]),
    projectSource({ runtimeId: "remote:p2", label: "个人博客", snapshot: null }),
  ] });
  expect(stale.warnings).toContain("项目「示例项目」暂时读不到最新状态，那边等你处理的事可能没有列全。");
  expect(stale.warnings).toContain("还没读到 「个人博客」，那边等你处理的事暂未列出。");
  expect(stale.decisions.find(item => item.title === "broken")).toMatchObject({ state: "unknown" });
});

it("ops check-ins that failed or went quiet stay listed from their state until the machine reports in", () => {
  const row = (source: string, status: OpsHeartbeat["status"], changes: Partial<OpsHeartbeat> = {}): OpsHeartbeat =>
    ({ source, status, text: "", last_ok: yesterday, last_report: yesterday, expect_hours: 26, ...changes });
  const model = buildAssistantWorkday({ ...input(), ops: { status: "current", checkedAt: now.getTime(), data: [
    row("nas-backup", "failed", { text: "NAS 未挂载" }), row("restore-drill", "overdue", { last_ok: new Date(now.getTime() - 40 * 3_600_000).toISOString() }),
    row("relay", "ok"), row("new-box", "overdue", { last_ok: "" }),
  ] } });
  expect(model.decisions.map(item => item.title)).toEqual(expect.arrayContaining(["运维 · nas-backup", "运维 · restore-drill", "运维 · new-box"]));
  expect(model.decisions.map(item => item.title)).not.toContain("运维 · relay");
  expect(model.decisions.find(item => item.title === "运维 · nas-backup")?.detail).toBe("最近一次报失败：NAS 未挂载");
  expect(model.decisions.find(item => item.title === "运维 · restore-drill")?.detail).toContain("已经 40 小时没有报到成功");
  expect(model.decisions.find(item => item.title === "运维 · new-box")?.detail).toContain("从没报到成功");
  expect(model.today.map(item => item.id)).toContain("ops:nas-backup");                   // 昨天坏的今天照样列着
  const old = buildAssistantWorkday({ ...input(), ops: { status: "unavailable", data: [] } });
  expect(old.warnings).toContain("运维报到未能取得最新状态（常驻电脑上的服务可能还没升级），备份等定时活的失败可能没有列出。");
});

it("self-heal failures from the last 24 hours need a decision; successes and older ones do not", () => {
  const recent = new Date(now.getTime() - 3_600_000).toISOString();
  const model = buildAssistantWorkday({ ...input(), notices: { status: "current", checkedAt: now.getTime(), data: [
    { ts: recent, source: "self-heal:relay", text: "⚠ 自愈未成功 [restart]：端口仍不通" },
    { ts: recent, source: "self-heal:relay", text: "🔧 自愈成功 [restart]：已重启" },
    { ts: new Date(now.getTime() - 30 * 3_600_000).toISOString(), source: "self-heal:disk", text: "⚠ 自愈未成功 [clean]：空间仍不足" },
    { ts: recent, source: "ops:nas-backup", text: "🔴 nas-backup 失败" },
  ] } });
  expect(model.decisions.map(item => item.title)).toEqual(["自动修复没有成功"]);
  expect(model.decisions[0].detail).toBe("[restart]：端口仍不通");
});
