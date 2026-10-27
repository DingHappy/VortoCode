import type { GatewayProcessStatus, RuntimeSnapshot } from "../types";

export function runtimeControlTarget(current: GatewayProcessStatus, stopped: GatewayProcessStatus | null,
  context: RuntimeSnapshot, remote: boolean): GatewayProcessStatus {
  return !remote && !current.running && stopped && stopped.scope === context.scope && stopped.workspaceRoot === context.workdir
    && (!current.runtimeId || current.runtimeId === stopped.runtimeId) ? stopped : current;
}

const COUNTS = ["turns", "queued_inputs", "confirmations", "tasks", "commands", "terminals", "writes"] as const;
type RuntimeActivity = { version: 1; ready: true; launch_id: string; scope: string; workspace_root: string;
  counts: Record<typeof COUNTS[number], number> };

function assertSameProcess(expected: GatewayProcessStatus, current: GatewayProcessStatus): void {
  if (!expected.running || !expected.runtimeId || !expected.launchId || !expected.workspaceRoot || !expected.baseUrl || !expected.scope
    || !current.running || current.runtimeId !== expected.runtimeId || current.launchId !== expected.launchId
    || current.scope !== expected.scope || current.workspaceRoot !== expected.workspaceRoot || current.baseUrl !== expected.baseUrl
    || expected.scope === "project" && (!expected.repoRoot || current.repoRoot !== expected.repoRoot)
    || expected.runtimeId.startsWith("remote:")) throw new Error("本机引擎身份已变化或不可核对，请刷新后重试；未执行停止");
}

export function assertRuntimeIdle(target: GatewayProcessStatus, value: unknown): void {
  const activity = value as RuntimeActivity | null;
  if (!activity || activity.version !== 1 || activity.ready !== true || !target.launchId
    || activity.launch_id !== target.launchId || activity.scope !== target.scope || activity.workspace_root !== target.workspaceRoot
    || !activity.counts || COUNTS.some(key => !Number.isSafeInteger(activity.counts[key]) || activity.counts[key] < 0)) {
    throw new Error("暂时无法核对本机引擎的活动状态，请稍后重试；未执行停止");
  }
  const labels: Record<typeof COUNTS[number], string> = { turns: "会话执行", queued_inputs: "排队输入", confirmations: "待答复确认",
    tasks: "后台任务", commands: "命令", terminals: "终端", writes: "写入请求" };
  const active = COUNTS.filter(key => activity.counts[key] > 0).map(key => `${labels[key]} ${activity.counts[key]}`);
  if (active.length) throw new Error(`引擎仍有活动（${active.join("、")}）；请先完成或停止这些工作，再重新加载或停止引擎`);
}

/** Recheck after confirmation; the native stop also fences the exact launch ID.
 * These observations are not an atomic quiescence lease across other clients.
 */
export async function withIdleRuntime(options: {
  expected: GatewayProcessStatus;
  current: () => boolean;
  readProcess: () => Promise<GatewayProcessStatus>;
  readActivity: (process: GatewayProcessStatus) => Promise<unknown>;
  confirm?: (process: GatewayProcessStatus) => Promise<boolean>;
  execute: (process: GatewayProcessStatus) => Promise<void>;
}): Promise<boolean> {
  const check = () => { if (!options.current()) throw new Error("工作区或连接已切换，本次引擎操作已取消"); };
  const readActivity = async (process: GatewayProcessStatus) => {
    try { return await options.readActivity(process); }
    catch { throw new Error("无法读取引擎活动状态；可能尚未就绪或不支持安全核对，本次未执行停止"); }
  };
  check();
  const first = await options.readProcess();
  assertSameProcess(options.expected, first); check();
  assertRuntimeIdle(first, await readActivity(first)); check();
  if (options.confirm && !await options.confirm(first)) return false;
  check();
  const latest = await options.readProcess();
  assertSameProcess(first, latest); check();
  assertRuntimeIdle(latest, await readActivity(latest)); check();
  await options.execute(latest);
  return true;
}
