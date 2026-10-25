import { afterEach, expect, it, vi } from "vitest";
import type { AgentTurnDetail } from "../types";
import { createActivityRecordReader, type ActivityRecordSnapshot } from "./activityRecordReader";
import { startPolling } from "./polling";

const record = (overrides: Partial<AgentTurnDetail> = {}): AgentTurnDetail => ({
  sid: "chat", rid: "turn", request: "核对", reply: "", has_reply: false, truncated: false, source: "session_archive",
  events: [{ type: "agent_tool", id: "read", rid: "turn", status: "running", args: { path: "README.md" } }], ...overrides,
});
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }
afterEach(() => vi.useRealTimers());

it("coalesces manual and automatic reads; preserves the last successful record and read time on failure", async () => {
  const first = deferred<AgentTurnDetail>();
  const call = vi.fn().mockReturnValueOnce(first.promise).mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(record({ reply: "已回复", has_reply: true }));
  const snapshots: ActivityRecordSnapshot[] = [];
  const reader = createActivityRecordReader({ client: { getAgentActivityDetail: call }, sid: "sid-chat", rid: "turn", onChange: value => snapshots.push(value), now: () => 100 });
  const pending = reader.refresh();
  expect(reader.refresh()).toBe(pending);
  await Promise.resolve(); expect(call).toHaveBeenCalledTimes(1);
  first.resolve(record()); expect(await pending).toBe(true);
  expect(reader.shouldPoll()).toBe(true);
  expect(await reader.refresh()).toBe(false);
  expect(reader.getSnapshot()).toMatchObject({ record: record(), checkedAt: 100, loading: false, error: expect.stringContaining("上次读取") });
  expect(await reader.refresh()).toBe(true);
  expect(reader.shouldPoll()).toBe(false);
  expect(reader.getSnapshot().error).toBe("");
  expect(snapshots.filter(value => value.checkedAt === 100)).not.toHaveLength(0);
});

it("never publishes a response after closing or starts another request after disposal", async () => {
  const response = deferred<AgentTurnDetail>(); const change = vi.fn();
  const call = vi.fn().mockReturnValue(response.promise);
  const reader = createActivityRecordReader({ client: { getAgentActivityDetail: call }, sid: "chat", rid: "turn", onChange: change });
  const pending = reader.refresh(); await Promise.resolve(); reader.dispose(); change.mockClear();
  response.resolve(record()); await pending;
  expect(change).not.toHaveBeenCalled(); expect(reader.shouldPoll()).toBe(false);
  await reader.refresh(); expect(call).toHaveBeenCalledTimes(1);
  const beforeSend = createActivityRecordReader({ client: { getAgentActivityDetail: call }, sid: "chat", rid: "turn", onChange: change });
  const notSent = beforeSend.refresh(); beforeSend.dispose(); await notSent;
  expect(call).toHaveBeenCalledTimes(1);
});

it.each([
  { sid: "other" }, { rid: "other" }, { events: [{ id: "foreign", rid: "other", type: "agent_tool" }] },
  { request: null }, { reply: {} }, { has_reply: "yes" }, { source: "unknown" },
])("rejects an invalid or foreign response without exposing it: %j", async patch => {
  const reader = createActivityRecordReader({ client: { getAgentActivityDetail: vi.fn().mockResolvedValue(record(patch as Partial<AgentTurnDetail>)) },
    sid: "chat", rid: "turn", onChange: () => {} });
  expect(await reader.refresh()).toBe(false);
  expect(reader.getSnapshot()).toMatchObject({ record: null, checkedAt: null, loading: false, error: expect.stringContaining("摘要") });
  expect(reader.shouldPoll()).toBe(false); // unsupported/404 services require manual retry
});

it("clears the loading state even if the transport throws synchronously", async () => {
  const reader = createActivityRecordReader({ client: { getAgentActivityDetail: () => { throw new Error("transport closed"); } }, sid: "chat", rid: "turn", onChange: () => {} });
  expect(await reader.refresh()).toBe(false); expect(reader.getSnapshot().loading).toBe(false);
});

it("polls an open unfinished record, stops network reads after a reply, and disposes the view cleanly", async () => {
  vi.useFakeTimers();
  const visibility = { hidden: false, addEventListener: vi.fn(), removeEventListener: vi.fn() };
  const call = vi.fn().mockResolvedValueOnce(record()).mockResolvedValue(record({ has_reply: true, reply: "有回复；步骤仍按真实状态显示" }));
  const reader = createActivityRecordReader({ client: { getAgentActivityDetail: call }, sid: "chat", rid: "turn", onChange: () => {} });
  await reader.refresh();
  const stop = startPolling(() => reader.shouldPoll() ? reader.refresh() : Promise.resolve(true), {
    intervalMs: 5_000, hiddenIntervalMs: 30_000, visibility,
  });
  await vi.advanceTimersByTimeAsync(5_000);
  expect(call).toHaveBeenCalledTimes(2);
  expect(reader.getSnapshot().record?.events[0].status).toBe("running");
  await vi.advanceTimersByTimeAsync(30_000); expect(call).toHaveBeenCalledTimes(2);
  stop(); reader.dispose();
  expect(visibility.removeEventListener).toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});
