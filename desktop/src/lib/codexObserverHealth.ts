import type { GatewayProcessStatus } from "../types";

const PHASES = ["not_started", "disabled", "starting", "connecting_codex", "connecting_resident", "observing", "reporting", "ready", "retrying", "waiting_codex", "superseded", "stopped"] as const;
export interface CodexObserverHealth {
  schema_version: 1; configured: boolean; run_id: string; phase: typeof PHASES[number];
  updated_at: number | null; last_observed_at: number | null; last_acknowledged_at: number | null;
  transport_mode: "unknown" | "legacy" | "ordered"; pending: boolean; error_code: string;
}
export type CodexObserverRead = { status: "current"; health: CodexObserverHealth; checkedAt: number }
  | { status: "loading" | "no_runtime" | "unavailable" | "unsupported" };
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const timestamp = (value: unknown) => value === null || (typeof value === "number" && Number.isFinite(value) && value >= 0);

export function parseCodexObserverHealth(raw: unknown): CodexObserverHealth | null {
  if (!record(raw) || raw.schema_version !== 1 || typeof raw.configured !== "boolean"
      || typeof raw.run_id !== "string" || typeof raw.phase !== "string" || !PHASES.includes(raw.phase as CodexObserverHealth["phase"])
      || ![raw.updated_at, raw.last_observed_at, raw.last_acknowledged_at].every(timestamp)
      || !["unknown", "legacy", "ordered"].includes(raw.transport_mode as string)
      || typeof raw.pending !== "boolean" || typeof raw.error_code !== "string") return null;
  if ((!raw.configured && raw.phase !== "disabled") || (raw.configured && raw.phase === "disabled")) return null;
  return { schema_version: 1, configured: raw.configured, run_id: raw.run_id, phase: raw.phase as CodexObserverHealth["phase"],
    updated_at: raw.updated_at as number | null, last_observed_at: raw.last_observed_at as number | null,
    last_acknowledged_at: raw.last_acknowledged_at as number | null, transport_mode: raw.transport_mode as CodexObserverHealth["transport_mode"],
    pending: raw.pending, error_code: raw.error_code };
}

function localGeneral(items: GatewayProcessStatus[]): GatewayProcessStatus | null {
  const sources = items.filter(item => item.running && item.scope === "general");
  if (sources.length !== 1) return null;
  const source = sources[0];
  if (!source.runtimeId || source.runtimeId.startsWith("remote:") || !source.baseUrl || !source.pid
      || !Number.isSafeInteger(source.pid) || source.pid <= 0) return null;
  try {
    const url = new URL(source.baseUrl);
    if (url.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(url.hostname)
        || url.username || url.password || url.search || url.hash || url.pathname !== "/") return null;
  } catch { return null; }
  return source;
}
const sourceKey = (source: GatewayProcessStatus) => JSON.stringify([source.runtimeId, source.pid, source.baseUrl, source.scope]);

/** Fresh native inventory selects the local producer, never the currently selected remote host. */
export async function readLocalCodexObserver(deps: {
  runtimes: () => Promise<GatewayProcessStatus[]>;
  read: (source: GatewayProcessStatus) => Promise<unknown>;
  now?: () => number;
}): Promise<CodexObserverRead> {
  try {
    const initial = await deps.runtimes();
    if (!Array.isArray(initial)) return { status: "unavailable" };
    const source = localGeneral(initial);
    if (!source) return { status: initial.some(item => item.running && item.scope === "general") ? "unavailable" : "no_runtime" };
    const selectedKey = sourceKey(source);
    const raw = await deps.read(source);
    const latest = localGeneral(await deps.runtimes());
    if (!latest || sourceKey(latest) !== selectedKey) return { status: "unavailable" };
    if (raw === undefined) return { status: "unsupported" };
    const health = parseCodexObserverHealth(raw);
    return health ? { status: "current", health, checkedAt: (deps.now ?? Date.now)() } : { status: "unavailable" };
  } catch { return { status: "unavailable" }; }
}

/** Coalesce manual/polling reads; a disposed card cannot publish late results. */
export function codexObserverReads(deps: {
  read: () => Promise<CodexObserverRead>; current: () => boolean; publish: (value: CodexObserverRead) => void;
}) {
  let pending: Promise<void> | null = null, epoch = 0;
  return {
    refresh() {
      if (!deps.current()) return Promise.resolve();
      if (pending) return pending;
      const generation = epoch;
      const task = deps.read().catch((): CodexObserverRead => ({ status: "unavailable" })).then(value => {
        if (deps.current() && epoch === generation) deps.publish(value);
      }).finally(() => { if (pending === task) pending = null; });
      pending = task;
      return task;
    },
    invalidate() { epoch++; pending = null; },
  };
}

const fresh = (stamp: number | null, now: number, maxAge = 120_000) => stamp !== null && stamp * 1000 <= now && now - stamp * 1000 <= maxAge;
export function codexObserverView(read: CodexObserverRead, now = Date.now()) {
  const result = (title: string, detail: string, acknowledgedAt: number | null = null) => ({ title, detail, acknowledgedAt });
  if (read.status === "loading") return result("正在核对本机观察员", "只读取运行状态，不启动或重启引擎。");
  if (read.status === "no_runtime") return result("本机通用引擎未运行", "观察员随本机通用引擎运行；保存开关不会立即启动它。");
  if (read.status === "unsupported") return result("当前引擎尚不提供观察员诊断", "请更新并重新打开 Desktop 后核对。");
  if (read.status !== "current" || !fresh(read.checkedAt / 1000, now)) return result("观察员状态待核对", "暂时无法读取本机通用引擎，请稍后刷新。");
  const h = read.health;
  if (!h.configured) return result("当前引擎未启用观察员", "检查常驻电脑连接和开关；保存修改后需退出并重新打开 Desktop。");
  if (h.phase === "not_started") return result("观察员尚未启动", "当前引擎已配置观察员，但尚无运行记录。");
  if (!h.run_id || !fresh(h.updated_at, now)) return result("观察员状态待核对", "观察循环尚无新鲜状态；历史送达记录不能代表当前仍在上报。");
  const ack = h.last_acknowledged_at !== null && h.last_acknowledged_at * 1000 <= now ? h.last_acknowledged_at : null;
  if (h.phase === "superseded") return result("此观察员已被接替", "当前实例已停止上报，不会自动抢回。请核对正在运行的其他 Desktop 实例。", ack);
  if (h.phase === "stopped") return result("观察员已停止", "此运行记录不代表 Codex 已停止执行。", ack);
  if (h.phase === "waiting_codex") return result("正在等待 Codex", "尚未发现本机 Codex 服务；请确认 Codex 正在运行。", ack);
  if (h.phase === "retrying") {
    const detail = h.error_code === "auth_failed" ? "常驻助理未接受身份验证，请核对常驻电脑连接配置。"
      : h.error_code === "protocol_error" ? "无法核对上报协议或回执，尚未降级发送。"
      : h.error_code === "codex_unavailable" ? "暂时无法读取本机 Codex，会继续核对。"
      : h.error_code === "actions_unavailable" ? "批准动作的状态暂时无法核对；最近送达时间单独列出。"
      : "暂时无法确认上报结果，会继续核对。";
    return result("观察员正在重试", detail + (h.pending ? " 原报告保留，等待确认后再发送最新快照。" : ""), ack);
  }
  if (h.phase === "ready") {
    if (!fresh(h.last_acknowledged_at, now, 30 * 60_000) || h.pending || h.transport_mode === "unknown") return result("观察员运行中，最近送达待核对", "已有运行记录，但近期送达或上报方式尚不能确认。", ack);
    return result("观察员运行中，最近上报已确认", h.transport_mode === "legacy"
      ? "常驻服务使用旧版上报方式，尚无顺序校验保证。" : "最近一份报告已获常驻助理确认；这不代表任务已经完成。", ack);
  }
  return result(h.phase === "reporting" ? "正在上报 Codex 进度" : "正在连接并核对进度", "尚未确认本次报告送达，请以确认记录为准。", ack);
}
