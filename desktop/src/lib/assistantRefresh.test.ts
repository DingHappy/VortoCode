import { expect, it } from "vitest";
import { AssistantRefreshQueue } from "./assistantRefresh";
import { AssistantActionGate } from "./assistantTask";

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(yes => { resolve = yes; });
  return { promise, resolve };
}

it("keeps explicit checks while polling coalesces and duplicate checks share a result", async () => {
  const queue = new AssistantRefreshQueue(), owner = {}, slow = deferred();
  const calls: string[] = [];
  const poll = queue.request(owner, "a", undefined, () => true, async () => { calls.push("poll"); await slow.promise; });
  const check = queue.request(owner, "a", "task-1", () => true, async () => { calls.push("check"); });
  expect(queue.request(owner, "a", "task-1", () => true, async () => { calls.push("duplicate"); })).toBe(check);
  expect(queue.request(owner, "a", undefined, () => true, async () => { calls.push("second poll"); })).toBe(poll);
  slow.resolve(); await Promise.all([poll, check]);
  expect(calls).toEqual(["poll", "check"]);
});

it("new connections do not wait for old requests, and queued stale work is skipped", async () => {
  const queue = new AssistantRefreshQueue(), old = {}, next = {}, slow = deferred();
  const calls: string[] = []; let current = true;
  const first = queue.request(old, "same scope", undefined, () => current, async () => { await slow.promise; });
  const queued = queue.request(old, "same scope", "task", () => current, async () => { calls.push("stale"); });
  current = false;
  await queue.request(next, "same scope", undefined, () => true, async () => { calls.push("new host"); });
  expect(calls).toEqual(["new host"]);
  slow.resolve(); await Promise.all([first, queued]); expect(calls).toEqual(["new host"]);
});

it("failures reach manual callers and release the queue for subsequent checks", async () => {
  const queue = new AssistantRefreshQueue(), owner = {};
  await expect(queue.request(owner, "a", "t1", () => true, async () => { throw new Error("offline"); })).rejects.toThrow("offline");
  let called = false;
  await queue.request(owner, "a", "t1", () => true, async () => { called = true; });
  expect(called).toBe(true);
});

it("bounds queued task checks instead of silently discarding them", async () => {
  const queue = new AssistantRefreshQueue(), owner = {}, slow = deferred();
  const requests = Array.from({ length: 16 }, (_, i) => queue.request(owner, "a", `t${i}`, () => true,
    async () => { if (i === 0) await slow.promise; }));
  await expect(queue.request(owner, "a", "overflow", () => true, async () => {})).rejects.toThrow("待核对任务较多");
  slow.resolve(); await Promise.all(requests);
});

it("two copies of a card cannot run duplicate confirmations and errors release the gate", async () => {
  const gate = new AssistantActionGate(), owner = {}, slow = deferred(); let calls = 0;
  const first = gate.run(owner, "host", "task", () => true, async () => { calls++; await slow.promise; });
  await expect(gate.run(owner, "host", "task", () => true, async () => { calls++; })).rejects.toThrow("正在处理");
  slow.resolve(); await first;
  await expect(gate.run(owner, "host", "task", () => true, async () => { throw new Error("failed"); })).rejects.toThrow("failed");
  await gate.run(owner, "host", "task", () => true, async () => { calls++; });
  await gate.run(owner, "old host", "task", () => false, async () => { calls++; });
  expect(calls).toBe(2);
});

it("an old connection's pending action does not block a new connection to the same host", async () => {
  const gate = new AssistantActionGate(), slow = deferred(); let newAction = false;
  const old = gate.run({}, "host", "task", () => true, async () => { await slow.promise; });
  await gate.run({}, "host", "task", () => true, async () => { newAction = true; });
  expect(newAction).toBe(true); slow.resolve(); await old;
});
