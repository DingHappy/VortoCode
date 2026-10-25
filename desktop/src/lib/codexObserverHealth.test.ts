import { expect, it, vi } from "vitest";
import type { GatewayProcessStatus } from "../types";
import { codexObserverReads, codexObserverView, parseCodexObserverHealth, readLocalCodexObserver,
  type CodexObserverHealth, type CodexObserverRead } from "./codexObserverHealth";

const now = 1_000_000;
function health(changes: Partial<CodexObserverHealth> = {}): CodexObserverHealth {
  return { schema_version: 1, configured: true, run_id: "loop-1", phase: "ready", updated_at: now / 1000,
    last_observed_at: now / 1000, last_acknowledged_at: now / 1000,
    transport_mode: "ordered", pending: false, error_code: "", ...changes };
}
const current = (changes: Partial<CodexObserverHealth> = {}): CodexObserverRead => ({ status: "current", checkedAt: now, health: health(changes) });
const runtime = (changes: Partial<GatewayProcessStatus> = {}): GatewayProcessStatus => ({ running: true, runtimeId: "general-1",
  scope: "general", pid: 123, baseUrl: "http://127.0.0.1:8080", message: "", ...changes });

it("projects known diagnostic fields and drops unrelated secrets", () => {
  const value = parseCodexObserverHealth({ ...health(), token: "private-token", target: "http://private-target" });
  expect(value).toEqual(health()); expect(JSON.stringify(value)).not.toContain("private-");
});
it.each([undefined, null, {}, { ...health(), schema_version: 2 }, { ...health(), schema_version: true },
  { ...health(), phase: "future_phase" }, { ...health(), updated_at: Infinity },
  { ...health(), last_acknowledged_at: -1 }, { ...health(), pending: "true" },
  { ...health(), configured: false }, { ...health(), phase: "disabled" },
])("malformed diagnostics never become healthy (%#)", value => {
  expect(parseCodexObserverHealth(value)).toBeNull();
});

it.each([ ["waiting_codex", "等待 Codex"], ["reporting", "正在上报"], ["superseded", "已被接替"],
  ["stopped", "已停止"], ["retrying", "正在重试"], ["ready", "上报已确认"],
] as const)("shows actual observer phase %s", (phase, text) => {
  expect(codexObserverView(current({ phase }), now).title).toContain(text);
});
it.each(["old loop", "future loop", "old read", "no ack", "future ack", "pending", "no run", "unknown transport"])(
  "never treats stale or unconfirmed diagnostics as ready: %s", fault => {
    const read = current();
    if (read.status !== "current") throw new Error("fixture");
    if (fault === "old loop") read.health.updated_at! -= 180;
    if (fault === "future loop") read.health.updated_at! += 1;
    if (fault === "old read") read.checkedAt -= 180_000;
    if (fault === "no ack") read.health.last_acknowledged_at = null;
    if (fault === "future ack") read.health.last_acknowledged_at! += 1;
    if (fault === "pending") read.health.pending = true;
    if (fault === "no run") read.health.run_id = "";
    if (fault === "unknown transport") read.health.transport_mode = "unknown";
    expect(codexObserverView(read, now).title).not.toContain("上报已确认");
  });
it("accounts for idle heartbeats and explains legacy transport without claiming task acceptance", () => {
  const view = codexObserverView(current({ last_acknowledged_at: now / 1000 - 10 * 60, transport_mode: "legacy" }), now);
  expect(view.title).toContain("上报已确认"); expect(view.detail).toContain("尚无顺序校验保证");
  expect(codexObserverView(current(), now).detail).toContain("不代表任务已经完成");
});
it("keeps configuration, observation and delivery distinct", () => {
  expect(codexObserverView(current({ configured: false, phase: "disabled" }), now).title).toContain("未启用");
  expect(codexObserverView(current({ phase: "not_started", updated_at: null }), now).title).toContain("尚未启动");
  const view = codexObserverView(current({ phase: "retrying", pending: true, error_code: "private-server-error" }), now);
  expect(view.detail).toContain("原报告保留"); expect(view.detail).not.toContain("private-");
});

it("selects local General instead of the selected project or resident", async () => {
  const sources = [runtime({ scope: "project", runtimeId: "project-1" }), runtime(),
    runtime({ scope: "project", runtimeId: "remote:host", baseUrl: "http://resident.test" })];
  const read = vi.fn(async () => health());
  const value = await readLocalCodexObserver({ runtimes: async () => sources, read, now: () => now });
  expect(read).toHaveBeenCalledOnce(); expect(read).toHaveBeenCalledWith(runtime());
  expect(value).toEqual(current());
});
it.each(["remote", "url credentials", "path", "missing pid", "stopped", "ambiguous"])("does not probe an unverified local producer: %s", async fault => {
  let source = runtime();
  if (fault === "remote") source.baseUrl = "http://resident.test";
  if (fault === "url credentials") source.baseUrl = "http://user:private-token@127.0.0.1:8080";
  if (fault === "path") source.baseUrl += "/other";
  if (fault === "missing pid") source.pid = undefined;
  if (fault === "stopped") source.running = false;
  const read = vi.fn(async () => health());
  const result = await readLocalCodexObserver({ runtimes: async () => fault === "ambiguous" ? [source, runtime({ pid: 124 })] : [source], read });
  expect(result.status).not.toBe("current"); expect(read).not.toHaveBeenCalled();
});
it("drops a response if the local process changed while reading", async () => {
  const source = runtime();
  const result = await readLocalCodexObserver({ runtimes: async () => [source], read: async () => {
    source.pid = 999; return health();
  } });
  expect(result.status).toBe("unavailable");
});
it("old runtimes with no diagnostic field remain unsupported", async () => {
  expect(await readLocalCodexObserver({ runtimes: async () => [runtime()], read: async () => undefined })).toEqual({ status: "unsupported" });
});
it("coalesces periodic and manual reads and ignores late responses after disposal", async () => {
  let resolve!: (value: CodexObserverRead) => void;
  const promise = new Promise<CodexObserverRead>(done => { resolve = done; });
  const publish = vi.fn(), read = vi.fn(() => promise);
  const reads = codexObserverReads({ read, publish, current: () => true });
  const first = reads.refresh(), second = reads.refresh();
  expect(first).toBe(second); expect(read).toHaveBeenCalledOnce();
  reads.invalidate(); resolve(current()); await first;
  expect(publish).not.toHaveBeenCalled();
});
