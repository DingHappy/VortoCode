import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";
import { invoke } from "@tauri-apps/api/core";
import { GatewayClient } from "./gateway";
import { assistantWorkdayReads } from "./lib/assistantWorkdayReads";

vi.mock("@tauri-apps/plugin-http", () => ({ fetch: vi.fn() }));
vi.mock("@tauri-apps/plugin-websocket", () => ({ default: { connect: vi.fn() } }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn() }));
const settings = { baseUrl: "http://127.0.0.1:8080", token: "" };
const reply = (data: object) => new Response(JSON.stringify(data));
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
};
beforeEach(() => { vi.resetAllMocks(); vi.useFakeTimers(); });
afterEach(() => vi.useRealTimers());

it("does not coalesce lifecycle preflight reads and rejects late replies after the deadline", async () => {
  const slow = deferred<Response>();
  vi.mocked(tauriFetch).mockReturnValueOnce(slow.promise).mockResolvedValueOnce(reply({ ready: true }));
  const client = new GatewayClient(settings);
  const first = client.getRuntimeActivity();
  const rejected = expect(first).rejects.toThrow("超时");
  expect(await client.getRuntimeActivity()).toEqual({ ready: true });
  expect(tauriFetch).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(5000);
  slow.resolve(reply({ ready: true }));
  await rejected;
  expect(vi.getTimerCount()).toBe(0);
});

it("coalesces only concurrent identical snapshots and fetches fresh data on the next read", async () => {
  const slow = deferred<Response>();
  vi.mocked(tauriFetch).mockReturnValueOnce(slow.promise).mockResolvedValueOnce(reply({ sessions: [] }));
  const client = new GatewayClient(settings);
  const first = client.listSessions(), second = client.listSessions();
  expect(tauriFetch).toHaveBeenCalledTimes(1);
  slow.resolve(reply({ sessions: [{ sid: "s1" }] }));
  expect(await first).toEqual(await second);
  expect(await client.listSessions()).toEqual([]);
  expect(tauriFetch).toHaveBeenCalledTimes(2);
  expect(vi.getTimerCount()).toBe(0);
});

it.each(["goals", "codex"] as const)("bounds a stalled workday %s read while publishing the available source", async stalledSource => {
  let signal!: AbortSignal;
  vi.mocked(tauriFetch).mockImplementation((url, init) => {
    const path = new URL(String(url)).pathname;
    const stalled = stalledSource === "goals" ? path === "/api/assistant/goals" : path === "/api/codex/status";
    if (stalled) {
      signal = init!.signal!;
      return new Promise<never>((_resolve, reject) => { signal.addEventListener("abort", () => reject(new Error("cancelled"))); });
    }
    return Promise.resolve(reply(path === "/api/assistant/goals" ? { goals: [] } : { overview: "", threads: [] }));
  });
  const client = new GatewayClient(settings);
  const goals = vi.fn(), codex = vi.fn();
  const reads = assistantWorkdayReads({ client: () => client, current: () => true, goals, codex });
  const first = reads.refresh();
  expect(reads.refresh()).toBe(first);
  await vi.advanceTimersByTimeAsync(0);
  expect(stalledSource === "goals" ? codex : goals).toHaveBeenCalledWith(expect.objectContaining({ status: "current" }));
  await vi.advanceTimersByTimeAsync(15_000); await first;
  expect(signal.aborted).toBe(true);
  expect(stalledSource === "goals" ? goals : codex).toHaveBeenLastCalledWith(expect.objectContaining({ status: "unavailable" }));
  vi.mocked(tauriFetch).mockImplementation(async url => reply(String(url).includes("/goals") ? { goals: [] } : { overview: "", threads: [] }));
  await reads.refresh();
  expect(goals).toHaveBeenLastCalledWith(expect.objectContaining({ status: "current" }));
  expect(codex).toHaveBeenLastCalledWith(expect.objectContaining({ status: "current" }));
  expect(vi.getTimerCount()).toBe(0);
});

it("keeps endpoints, query parameters and client identities separate", async () => {
  vi.mocked(tauriFetch).mockImplementation(async () => reply({}));
  const a = new GatewayClient(settings), b = new GatewayClient({ ...settings, baseUrl: "http://127.0.0.1:8081" });
  await Promise.all([a.listSessions(), a.listRuns(), a.listAudit(10), a.listAudit(20), a.listDecisions("s1"), a.listDecisions("s2"), b.listSessions()]);
  expect(tauriFetch).toHaveBeenCalledTimes(7);
  expect(vi.getTimerCount()).toBe(0);
});

it.each(["headers", "body"])("aborts a stalled %s read and permits a later retry", async phase => {
  let signal!: AbortSignal;
  let cancelled = 0;
  const stalled = () => new Promise<never>((_resolve, reject) => {
    signal.addEventListener("abort", () => { cancelled++; reject(new Error("cancelled")); }, { once: true });
  });
  vi.mocked(tauriFetch).mockImplementationOnce((_url, init) => {
    signal = init!.signal!;
    return phase === "headers" ? stalled() : Promise.resolve({ ok: true, json: stalled } as unknown as Response);
  }).mockResolvedValueOnce(reply({ sessions: [] }));
  const client = new GatewayClient(settings);
  const pending = client.listSessions();
  const checked = expect(pending).rejects.toThrow("读取运行状态超时");
  await vi.advanceTimersByTimeAsync(15_000);
  await checked;
  expect(signal.aborted).toBe(true);
  expect(cancelled).toBe(1);
  expect(await client.listSessions()).toEqual([]);
  expect(tauriFetch).toHaveBeenCalledTimes(2);
  expect(vi.getTimerCount()).toBe(0);
});

it("releases failed reads and clears their deadline", async () => {
  vi.mocked(tauriFetch).mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(reply({ runs: [] }));
  const client = new GatewayClient(settings);
  await expect(client.listRuns()).rejects.toThrow("offline");
  expect(vi.getTimerCount()).toBe(0);
  expect(await client.listRuns()).toEqual([]);
});

it("a write starts a fresh snapshot generation without merging or replaying the write", async () => {
  const old = deferred<Response>(), fresh = deferred<Response>();
  vi.mocked(tauriFetch).mockReturnValueOnce(old.promise)
    .mockResolvedValueOnce(reply({})).mockReturnValueOnce(fresh.promise);
  const client = new GatewayClient(settings);
  const first = client.listSessions();
  await client.renameSession("s1", "new name");
  const second = client.listSessions();
  expect(tauriFetch).toHaveBeenCalledTimes(3);
  expect(vi.mocked(tauriFetch).mock.calls[1][1]?.signal).toBeUndefined();
  old.resolve(reply({ sessions: [{ sid: "old" }] })); await first;
  // 旧读取的 finally 不能移除写入后的进行中读取。
  const third = client.listSessions();
  expect(tauriFetch).toHaveBeenCalledTimes(3);
  fresh.resolve(reply({ sessions: [{ sid: "fresh" }] }));
  expect(await second).toEqual([{ sid: "fresh" }]);
  expect(await third).toEqual([{ sid: "fresh" }]);
});

it("disconnect aborts local reads and rejects a transport's late response", async () => {
  const late = deferred<Response>();
  let signal!: AbortSignal;
  vi.mocked(tauriFetch).mockImplementationOnce((_url, init) => { signal = init!.signal!; return late.promise; });
  const client = new GatewayClient(settings);
  const pending = client.listSessions();
  await client.disconnect();
  expect(signal.aborted).toBe(true);
  late.resolve(reply({ sessions: [{ sid: "obsolete" }] }));
  await expect(pending).rejects.toThrow("连接已切换");
  expect(vi.getTimerCount()).toBe(0);
});

it("remote reads retain the native transport deadline and cannot publish after disconnect", async () => {
  const slow = deferred<{ status: number; body: string }>();
  vi.mocked(invoke).mockReturnValueOnce(slow.promise);
  const client = new GatewayClient({ ...settings, remoteProjectId: "registered" });
  const a = client.listSessions(), b = client.listSessions();
  expect(invoke).toHaveBeenCalledTimes(1);
  expect(tauriFetch).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
  await client.disconnect();
  slow.resolve({ status: 200, body: JSON.stringify({ sessions: [] }) });
  await expect(a).rejects.toThrow("连接已切换");
  await expect(b).rejects.toThrow("连接已切换");
});
