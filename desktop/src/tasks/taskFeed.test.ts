import { describe, expect, it, vi } from "vitest";
import type { ProtocolEvent, TaskItem, WorktreeWorkspaceSnapshot } from "../types";
import { TaskFeed, type TaskClient } from "./taskFeed";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const task = (id: string, status = "running"): TaskItem => ({ id, status });
const event = (type: string, data: unknown): ProtocolEvent => ({ type, data });
const tick = async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); };
const serviceTask = (id: string, status = "done"): TaskItem => ({
  ...task(id, status), collaboration: { assignee: "research", round: 1, review: "accepted",
    acceptance: [], tainted: false, messages: [], dispatch: { source: "api", max_steps: 4, timeout_seconds: 30 } },
});
function client(): TaskClient {
  return { listTasks: vi.fn(async () => []),
    getWorktreeWorkspace: vi.fn(async () => ({ worktrees: [], plans: [] })),
    cancelTask: vi.fn(), pauseTask: vi.fn(), resumeTask: vi.fn() };
}

describe("task feed request and event ownership", () => {
  it("keeps a newer live update when an older HTTP snapshot arrives", async () => {
    const source = client();
    const list = deferred<TaskItem[]>();
    source.listTasks = () => list.promise;
    const feed = new TaskFeed(() => source);
    const fetching = feed.refreshTasks();
    feed.acceptEvent(event("task_update", task("a", "done")), source);
    list.resolve([task("a", "running")]);
    await fetching;
    expect(feed.getSnapshot().tasks).toEqual([task("a", "done")]);
    // A later read can authoritatively remove rows absent from the server.
    source.listTasks = async () => [];
    await feed.refreshTasks();
    expect(feed.getSnapshot().tasks).toEqual([]);
  });

  it("lets the latest request win even if responses arrive in reverse order", async () => {
    const source = client();
    const first = deferred<TaskItem[]>(), second = deferred<TaskItem[]>();
    source.listTasks = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const feed = new TaskFeed(() => source);
    const oldRead = feed.refreshTasks(), newRead = feed.refreshTasks();
    second.resolve([task("new")]);
    await newRead;
    first.resolve([task("old")]);
    await oldRead;
    expect(feed.getSnapshot().tasks).toEqual([task("new")]);
  });

  it("rejects events and pending reads from a previous connection", async () => {
    const previous = client(), next = client();
    let active = previous;
    const list = deferred<TaskItem[]>();
    previous.listTasks = () => list.promise;
    const feed = new TaskFeed(() => active);
    const fetching = feed.refreshTasks();
    const commandScope = feed.capture();
    active = next;
    expect(feed.acceptEvent(event("task_handoff", task("old")), previous)).toBe(false);
    feed.acceptEvent(event("task_snapshot", [task("new")]), next);
    list.resolve([task("old")]);
    await fetching;
    expect(commandScope.current()).toBe(false);
    expect(feed.getSnapshot().tasks).toEqual([task("new")]);
  });

  it("reset invalidates reads and command completion even on the same connection", async () => {
    const source = client();
    const list = deferred<TaskItem[]>();
    source.listTasks = () => list.promise;
    const feed = new TaskFeed(() => source);
    feed.focus("old-focus");
    const commandScope = feed.capture();
    const fetching = feed.refreshTasks();
    feed.reset();
    list.resolve([task("old")]);
    await fetching;
    expect(commandScope.current()).toBe(false);
    expect(feed.getSnapshot()).toEqual({ tasks: [], focusedTaskId: "", worktreeWorkspace: { worktrees: [], plans: [] } });
  });

  it("isolates worktree reads from resets and reversed response order", async () => {
    const source = client();
    const first = deferred<WorktreeWorkspaceSnapshot>(), second = deferred<WorktreeWorkspaceSnapshot>();
    source.getWorktreeWorkspace = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const feed = new TaskFeed(() => source);
    const oldRead = feed.refreshWorktrees();
    feed.reset();
    const newRead = feed.refreshWorktrees();
    const current = { worktrees: [], plans: [] };
    second.resolve(current);
    await newRead;
    first.resolve({ worktrees: [], plans: [] });
    await oldRead;
    expect(feed.getSnapshot().worktreeWorkspace).toBe(current);
  });

  it("deduplicates repeated handoff and command results using server task IDs", () => {
    const source = client();
    const feed = new TaskFeed(() => source);
    const observer = vi.fn();
    const unsubscribe = feed.subscribe(observer);
    feed.acceptEvent(event("task_update", task("a")), source);
    feed.acceptEvent(event("task_handoff", task("a", "done")), source);
    feed.upsert(task("a", "paused"), source);
    expect(feed.getSnapshot().tasks).toEqual([task("a", "paused")]);
    expect(observer).toHaveBeenCalledTimes(3);
    unsubscribe();
    feed.focus("a");
    expect(observer).toHaveBeenCalledTimes(3);
  });

  it("preserves live data when HTTP is unavailable", async () => {
    const source = client();
    const list = deferred<TaskItem[]>();
    source.listTasks = () => list.promise;
    const feed = new TaskFeed(() => source);
    feed.acceptEvent(event("task_snapshot", [task("a")]), source);
    const fetching = feed.refreshTasks();
    list.reject(new Error("offline"));
    await fetching;
    expect(feed.getSnapshot().tasks).toEqual([task("a")]);
  });

  it("refreshes sibling allowances from the server and rejects an older chain read", async () => {
    const source = client();
    const first = deferred<TaskItem[]>(), second = deferred<TaskItem[]>();
    source.listTasks = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const feed = new TaskFeed(() => source);
    const budget = (id: string, rounds: number, reserved: number[]): TaskItem => ({
      ...task(id), owner_session: "owner", chain_budget: {
        available: true, root_task_id: "root", used: { tasks: 3, rounds, steps: rounds * 4, timeout_seconds: rounds * 30 },
        reserved_rounds: reserved,
      },
    });
    feed.acceptEvent(event("task_snapshot", [budget("root", 1, [1]), budget("sibling", 1, [])]), source);
    feed.acceptEvent(event("task_update", budget("child", 2, [1])), source);
    await tick();
    feed.acceptEvent(event("task_handoff", budget("child", 3, [1, 2])), source);
    const latest = [budget("root", 3, [1]), budget("child", 3, [1, 2]), budget("sibling", 3, [])];
    first.resolve([budget("root", 2, [1])]);
    await tick();
    second.resolve(latest);
    await tick();
    expect(source.listTasks).toHaveBeenCalledTimes(2);
    expect(feed.getSnapshot().tasks).toEqual(latest);
  });

  it("isolates a chain event refresh when the connection changes", async () => {
    const previous = client(), next = client();
    let active = previous;
    const list = deferred<TaskItem[]>();
    previous.listTasks = vi.fn(() => list.promise);
    const feed = new TaskFeed(() => active);
    feed.acceptEvent(event("task_update", { ...task("old"), chain_budget: { available: false, error: "missing root" } }), previous);
    await tick();
    active = next;
    feed.reset();
    feed.acceptEvent(event("task_snapshot", [task("new")]), next);
    list.resolve([task("old")]);
    await tick();
    expect(previous.listTasks).toHaveBeenCalledTimes(1);
    expect(feed.getSnapshot().tasks).toEqual([task("new")]);
  });

  it("refreshes unbudgeted descendants from the server after a source event", async () => {
    const source = client();
    const child = { ...serviceTask("child"), dependencies: { resolution: "consumed" as const, result_valid: true } };
    const stale = { ...child, dependencies: { ...child.dependencies, result_valid: false, can_reconcile: true } };
    source.listTasks = vi.fn(async () => [serviceTask("root"), stale]);
    const feed = new TaskFeed(() => source);
    feed.acceptEvent(event("task_snapshot", [serviceTask("root"), child]), source);
    feed.acceptEvent(event("task_update", serviceTask("root")), source);
    feed.acceptEvent(event("task_handoff", serviceTask("root")), source);
    await tick();
    expect(source.listTasks).toHaveBeenCalledTimes(1);
    expect(feed.getSnapshot().tasks.find((row) => row.id === "child")).toEqual(stale);
  });

  it("retries a projection discarded by an unrelated event without overwriting it", async () => {
    const source = client();
    const first = deferred<TaskItem[]>(), second = deferred<TaskItem[]>();
    source.listTasks = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const feed = new TaskFeed(() => source);
    const budgeted = { ...task("root"), chain_budget: { available: false, error: "changed" } };
    feed.acceptEvent(event("task_update", budgeted), source);
    await tick();
    feed.acceptEvent(event("task_update", task("other", "done")), source);
    first.resolve([budgeted, task("other", "running")]);
    await tick();
    expect(feed.getSnapshot().tasks.find((row) => row.id === "other")?.status).toBe("done");
    expect(source.listTasks).toHaveBeenCalledTimes(2);
    const latest = [budgeted, task("sibling", "done"), task("other", "done")];
    second.resolve(latest);
    await tick();
    expect(feed.getSnapshot().tasks).toEqual(latest);
  });

  it("coalesces an event burst into one in-flight read and one pending read", async () => {
    const source = client();
    const first = deferred<TaskItem[]>(), second = deferred<TaskItem[]>();
    source.listTasks = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const feed = new TaskFeed(() => source);
    feed.acceptEvent(event("task_update", serviceTask("root")), source);
    await tick();
    for (let i = 0; i < 100; i++) feed.acceptEvent(event("task_update", serviceTask(`child-${i}`)), source);
    await tick();
    expect(source.listTasks).toHaveBeenCalledTimes(1);
    first.resolve([]);
    await tick();
    expect(source.listTasks).toHaveBeenCalledTimes(2);
    second.resolve([serviceTask("final")]);
    await tick();
    expect(feed.getSnapshot().tasks).toEqual([serviceTask("final")]);
  });

  it("refreshes waiting readiness without a chain allowance or local source row", async () => {
    const source = client();
    const waiting = { ...serviceTask("grandchild", "waiting"), dependencies: { resolution: "waiting" as const, ready: false } };
    const ready = { ...waiting, dependencies: { ...waiting.dependencies, ready: true } };
    source.listTasks = vi.fn(async () => [ready]);
    const feed = new TaskFeed(() => source);
    feed.acceptEvent(event("task_snapshot", [waiting]), source);
    feed.acceptEvent(event("task_update", serviceTask("ancestor")), source);
    await tick();
    expect(feed.getSnapshot().tasks).toEqual([ready]);
  });

  it("drops an event refresh queued before reset without making a request", async () => {
    const source = client();
    const feed = new TaskFeed(() => source);
    feed.acceptEvent(event("task_update", serviceTask("old")), source);
    feed.reset();
    await tick();
    expect(source.listTasks).not.toHaveBeenCalled();
    expect(feed.getSnapshot().tasks).toEqual([]);
  });

  it("drops the dirty follow-up when a full live snapshot has arrived", async () => {
    const source = client();
    const list = deferred<TaskItem[]>();
    source.listTasks = vi.fn(() => list.promise);
    const feed = new TaskFeed(() => source);
    feed.acceptEvent(event("task_update", serviceTask("root")), source);
    await tick();
    feed.acceptEvent(event("task_update", serviceTask("child")), source);
    feed.acceptEvent(event("task_snapshot", [serviceTask("current")]), source);
    list.resolve([serviceTask("old")]);
    await tick();
    expect(source.listTasks).toHaveBeenCalledTimes(1);
    expect(feed.getSnapshot().tasks).toEqual([serviceTask("current")]);
  });

  it("retains rows on a failed event read and waits for a new event to retry", async () => {
    const source = client();
    source.listTasks = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce([serviceTask("new")]);
    const feed = new TaskFeed(() => source);
    feed.acceptEvent(event("task_update", serviceTask("root")), source);
    await tick();
    expect(source.listTasks).toHaveBeenCalledTimes(1);
    expect(feed.getSnapshot().tasks).toEqual([serviceTask("root")]);
    feed.acceptEvent(event("task_handoff", serviceTask("root")), source);
    await tick();
    expect(source.listTasks).toHaveBeenCalledTimes(2);
    expect(feed.getSnapshot().tasks).toEqual([serviceTask("new")]);
  });
});
