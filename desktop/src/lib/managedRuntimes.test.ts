import { describe, expect, it } from "vitest";

import type { DesktopProjectProfile, GatewayProcessStatus, GatewayRecoveryRecord } from "../types";
import { ManagedRuntimes } from "./managedRuntimes";

const process = (runtimeId: string, running: boolean, patch: Partial<GatewayProcessStatus> = {}): GatewayProcessStatus =>
  ({ runtimeId, running, message: "", baseUrl: "http://127.0.0.1:8080", ...patch });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

/** 假 Tauri：按命令名返回；记下每次调用。 */
function harness(handlers: Record<string, (args?: Record<string, unknown>) => unknown>) {
  const calls: Array<[string, Record<string, unknown> | undefined]> = [];
  const state = { status: null as GatewayProcessStatus | null, processes: null as GatewayProcessStatus[] | null,
    recoveries: null as GatewayRecoveryRecord[] | null, record: undefined as GatewayRecoveryRecord | null | undefined };
  const invoke = async <T,>(command: string, args?: Record<string, unknown>): Promise<T> => {
    calls.push([command, args]);
    const handler = handlers[command];
    if (!handler) throw new Error(`unexpected ${command}`);
    return await handler(args) as T;
  };
  const runtimes = new ManagedRuntimes(invoke, {
    setProcessStatus: (status) => { state.status = status; },
    setRuntimeProcesses: (items) => { state.processes = items; },
    setRuntimeRecoveries: (items) => { state.recoveries = items; },
    setRecoveryRecord: (record) => { state.record = record; },
  });
  return { runtimes, state, calls };
}

const launchArgs = { scope: "project" as const, repoRoot: "/code/app", workspaceId: "s1", port: 8080 };

describe("launch", () => {
  it("records the token only when one is given or none is known yet", async () => {
    const { runtimes } = harness({
      start_gateway: () => process("rt", true), list_gateway_processes: () => [], get_gateway_recovery: () => null, list_gateway_recoveries: () => [],
    });
    await runtimes.launch({ ...launchArgs, token: null });
    expect(runtimes.tokenFor("rt")).toBe("");
    await runtimes.launch({ ...launchArgs, token: "secret" });
    expect(runtimes.tokenFor("rt")).toBe("secret");
    await runtimes.launch({ ...launchArgs, token: null });
    expect(runtimes.tokenFor("rt")).toBe("secret");
  });

  it("falls back to the launched process when the process list cannot be read", async () => {
    const launched = process("rt", true);
    const { runtimes, state } = harness({
      start_gateway: () => launched, list_gateway_processes: () => { throw new Error("ipc"); },
      get_gateway_recovery: () => null, list_gateway_recoveries: () => [],
    });
    await runtimes.launch({ ...launchArgs, token: null });
    expect(state.status).toBe(launched);
    expect(state.processes).toEqual([launched]);
  });
});

describe("supervision", () => {
  const stopped = () => Promise.resolve();

  it("flags a runtime that was running and is gone, after the caller has had a chance to disconnect", async () => {
    let statuses = [process("rt", true)];
    const order: string[] = [];
    const { runtimes, state } = harness({
      start_gateway: () => process("rt", true), list_gateway_processes: () => statuses, list_gateway_recoveries: () => [],
      get_gateway_recovery: () => { order.push("recovery"); return { runtimeId: "rt" } as GatewayRecoveryRecord; },
    });
    await runtimes.launch({ ...launchArgs, token: null });
    expect(await runtimes.supervise("rt", () => false, stopped)).toBeNull();
    statuses = [process("rt", false, { message: "进程退出（code 1）" })];
    const message = await runtimes.supervise("rt", () => false, async () => { order.push("disconnect"); });
    expect(message).toBe("进程退出（code 1）");
    expect(order.slice(-2)).toEqual(["disconnect", "recovery"]);
    expect(state.record).toEqual({ runtimeId: "rt" });
  });

  it("a stop the user asked for is not an unexpected exit", async () => {
    let statuses = [process("rt", true)];
    const { runtimes } = harness({
      start_gateway: () => process("rt", true), list_gateway_processes: () => statuses, list_gateway_recoveries: () => [],
      get_gateway_recovery: () => null, stop_gateway: () => process("rt", false),
    });
    await runtimes.launch({ ...launchArgs, token: "t" });
    await runtimes.stopCurrent("rt");
    statuses = [];
    expect(await runtimes.supervise("rt", () => false, async () => { throw new Error("should not be called"); })).toBeNull();
    expect(runtimes.tokenFor("rt")).toBe("");
  });

  it("drops a poll that was overtaken by a start, stop or switch", async () => {
    const slow = deferred<GatewayProcessStatus[]>();
    const { runtimes, state } = harness({ list_gateway_processes: () => slow.promise, list_gateway_recoveries: () => [] });
    const pending = runtimes.supervise("rt", () => false, stopped);
    runtimes.invalidate();
    slow.resolve([process("rt", true)]);
    expect(await pending).toBeNull();
    expect(state.processes).toBeNull();
  });

  it("does not supervise remote workspaces as managed processes, and reports IPC failures for backoff", async () => {
    const { runtimes, state } = harness({ list_gateway_processes: () => [], list_gateway_recoveries: () => [] });
    runtimes.markRemote({ id: "p", name: "p", kind: "remote", repoRoot: "/srv", baseUrl: "https://x", lastOpenedAt: 0 } as DesktopProjectProfile, false);
    expect(await runtimes.supervise("remote:p", () => false, stopped)).toBeNull();
    expect(state.status?.runtimeId).toBe("remote:p");
    const broken = harness({ list_gateway_processes: () => { throw new Error("ipc"); } });
    expect(await broken.runtimes.supervise("rt", () => false, stopped)).toBe(false);
  });
});

it("a restart refuses to continue when the runtime did not actually stop", async () => {
  const { runtimes } = harness({ stop_gateway: () => process("rt", true) });
  await expect(runtimes.stopForRestart("rt")).rejects.toThrow("模型配置尚未生效");
});
