import { expect, it, vi } from "vitest";
import { assertRuntimeIdle, runtimeControlTarget, withIdleRuntime } from "./runtimeLifecycle";
import type { GatewayProcessStatus } from "../types";

const process: GatewayProcessStatus = { runtimeId: "general", running: true, launchId: "launch-a", scope: "general",
  baseUrl: "http://127.0.0.1:8123", workspaceRoot: "/fixture/general", message: "" };
const activity = () => ({ version: 1, ready: true, launch_id: "launch-a", scope: "general", workspace_root: "/fixture/general",
  counts: { turns: 0, queued_inputs: 0, confirmations: 0, tasks: 0, commands: 0, terminals: 0, writes: 0 } });

it("preserves a stopped Scratch workspace identity only in that same local context", () => {
  const stopped: GatewayProcessStatus = { ...process, running: false, scope: "scratch", workspaceRoot: "/fixture/scratch-a", workspaceId: "scratch-a" };
  const empty = { running: false, message: "" };
  const context = { scope: "scratch" as const, workdir: "/fixture/scratch-a" };
  expect(runtimeControlTarget(empty, stopped, context, false).workspaceId).toBe("scratch-a");
  expect(runtimeControlTarget(empty, stopped, context, true)).toBe(empty);
  expect(runtimeControlTarget(empty, stopped, { ...context, workdir: "/fixture/scratch-b" }, false)).toBe(empty);
  expect(runtimeControlTarget(process, stopped, context, false)).toBe(process);
  const other = { ...empty, runtimeId: "other" };
  expect(runtimeControlTarget(other, stopped, context, false)).toBe(other);
});
function fixture() {
  return { expected: process, current: () => true, readProcess: vi.fn(async () => process),
    readActivity: vi.fn(async (): Promise<unknown> => activity()), confirm: vi.fn(async () => true), execute: vi.fn(async () => {}) };
}

it("rechecks the exact process and activity after confirmation before one stop", async () => {
  const options = fixture();
  expect(await withIdleRuntime(options)).toBe(true);
  expect(options.readProcess).toHaveBeenCalledTimes(2);
  expect(options.readActivity).toHaveBeenCalledTimes(2);
  expect(options.execute).toHaveBeenCalledExactlyOnceWith(process);
});

it.each(["turns", "queued_inputs", "confirmations", "tasks", "commands", "terminals", "writes"] as const)("preserves an engine with active %s", async key => {
  const options = fixture(); const busy = activity(); busy.counts[key] = 1;
  options.readActivity.mockResolvedValue(busy);
  await expect(withIdleRuntime(options)).rejects.toThrow("仍有活动");
  expect(options.confirm).not.toHaveBeenCalled();
  expect(options.execute).not.toHaveBeenCalled();
});

it("cancels without stopping and blocks activity that appeared while the sheet was open", async () => {
  const cancelled = fixture(); cancelled.confirm.mockResolvedValue(false);
  expect(await withIdleRuntime(cancelled)).toBe(false);
  expect(cancelled.execute).not.toHaveBeenCalled();
  const changed = fixture(); const busy = activity(); busy.counts.tasks = 1;
  changed.readActivity.mockResolvedValueOnce(activity()).mockResolvedValueOnce(busy);
  await expect(withIdleRuntime(changed)).rejects.toThrow("后台任务");
  expect(changed.execute).not.toHaveBeenCalled();
});

it("rejects a switched scope, replacement launch and a failed or old activity API", async () => {
  const switched = fixture(); let current = true;
  switched.current = () => current;
  switched.confirm.mockImplementation(async () => { current = false; return true; });
  await expect(withIdleRuntime(switched)).rejects.toThrow("工作区或连接已切换");
  expect(switched.execute).not.toHaveBeenCalled();
  const replaced = fixture();
  replaced.readProcess.mockResolvedValueOnce(process).mockResolvedValueOnce({ ...process, launchId: "launch-b" });
  await expect(withIdleRuntime(replaced)).rejects.toThrow("身份已变化");
  expect(replaced.execute).not.toHaveBeenCalled();
  const failed = fixture(); failed.readActivity.mockRejectedValue(new Error("404 private error"));
  await expect(withIdleRuntime(failed)).rejects.toThrow("不支持安全核对");
  expect(failed.execute).not.toHaveBeenCalled();
});

it("refuses incomplete, future or misbound snapshots, including nonnumeric counters", () => {
  for (const invalid of [null, {}, { ...activity(), ready: false }, { ...activity(), version: 2 },
    { ...activity(), launch_id: "other" }, { ...activity(), scope: "project" }, { ...activity(), workspace_root: "/other" },
    { ...activity(), counts: {} }, { ...activity(), counts: { ...activity().counts, tasks: -1 } },
    { ...activity(), counts: { ...activity().counts, tasks: true } }]) {
    expect(() => assertRuntimeIdle(process, invalid)).toThrow("无法核对");
  }
});
