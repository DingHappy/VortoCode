/**
 * 跨项目收件箱的状态归类——把"还没起来"和"连上过又断了"分开。
 *
 * 真机诊断（2026-09-17）：刚加完项目、runtime 正在启动的那十几秒里，轮询自然失败，界面却直接
 * 报「1 个失联」。失联是要人去查的故障，启动中是等一会儿就好——两者长一个样，用户学会的是
 * 忽略这个提示，于是真失联时也不会去看。
 *
 * 判据只有两条，都不依赖文案匹配：这个 runtime **成功连上过吗**，以及**是不是刚出现**。
 */

export type RuntimeReachability = "ok" | "starting" | "unreachable";

/** 首次出现后多久仍连不上就按失联算。真机上项目 runtime 冷启动约 3 秒，留足余量。 */
export const STARTING_GRACE_MS = 20_000;

export type ReachabilityInput = {
  error?: string;
  firstSeenAt: number;
  everReachable: boolean;
};

export function classifyReachability(item: ReachabilityInput, now: number): RuntimeReachability {
  if (!item.error) return "ok";
  if (item.everReachable) return "unreachable";   // 连上过 → 现在连不上就是真故障
  return now - item.firstSeenAt < STARTING_GRACE_MS ? "starting" : "unreachable";
}

export type InboxCounts = {
  decisions: number;
  hook_issues: number;
  goals_blocked: number;
  goals_active: number;
  tasks_attention: number;
  tasks_active: number;
  sessions_working: number;
  sessions_queued: number;
};

export type SummaryInput = ReachabilityInput & { counts?: InboxCounts | null };

export type InboxSummary = {
  runtimes: number;
  unreachable: number;
  starting: number;
  actionable: number;
  working: number;
  queued: number;
  goals: number;
  tasks: number;
};

export function summarizeRuntimeInboxes(items: SummaryInput[], now: number): InboxSummary {
  const summary: InboxSummary = {
    runtimes: items.length,
    unreachable: 0,
    starting: 0,
    actionable: 0,
    working: 0,
    queued: 0,
    goals: 0,
    tasks: 0,
  };
  items.forEach((item) => {
    const reachability = classifyReachability(item, now);
    if (reachability === "unreachable") summary.unreachable += 1;
    if (reachability === "starting") summary.starting += 1;
    const counts = item.counts;
    if (!counts) return;
    summary.actionable += counts.decisions + counts.hook_issues
      + counts.goals_blocked + counts.tasks_attention;
    summary.working += counts.sessions_working;
    summary.queued += counts.sessions_queued;
    summary.goals += counts.goals_active + counts.goals_blocked;
    summary.tasks += counts.tasks_active + counts.tasks_attention;
  });
  return summary;
}

/** 收件箱副标题那一行：优先说要处理的，其次是真故障，再次是启动中，最后才是执行中。 */
export function runtimeInboxSubtitle(summary: InboxSummary): string {
  const workspaces = `${summary.runtimes} 个工作区`;
  if (summary.actionable > 0) return `${workspaces} · ${summary.actionable} 项待处理`;
  if (summary.unreachable > 0) return `${workspaces} · ${summary.unreachable} 个失联`;
  if (summary.starting > 0) return `${workspaces} · ${summary.starting} 个启动中`;
  return `${workspaces} · ${summary.working} 个执行中`;
}
