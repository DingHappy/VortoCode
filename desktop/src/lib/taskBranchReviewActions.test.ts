import { expect, it, vi } from "vitest";
import type { GatewayClient } from "../gateway";
import type { GitReviewHunk, TaskBranchReviewSnapshot, TaskBranchReviewDiff, TaskItem } from "../types";
import { taskBranchReviewActions, type TaskReviewView } from "./taskBranchReviewActions";
import { ViewMutations } from "./viewRequests";

const HEAD = "a".repeat(40);
const task = (id = "a"): TaskItem => ({ id, status: "done", branch: `vorto/${id}` });
const snapshot = (id = "a"): TaskBranchReviewSnapshot => ({ ok: true, root: "/repo", branch: `vorto/${id}`,
  head: HEAD.slice(0, 12), head_oid: HEAD, target: "task", task_id: id, base: "main", mutable: true, mutation_reason: "",
  review: { accepted_hunks: 0, verification_stale: false }, truncated: false,
  files: [{ path: "a.py", status: "M", index_status: " ", worktree_status: "M", staged: false, unstaged: true, untracked: false, conflicted: false }] });
const diff = (id = "a", path = "a.py"): TaskBranchReviewDiff => ({ ok: true, target: "task", task_id: id,
  scope: "branch", path, head: HEAD, baseline: `${id}:${path}`, hunks: [], binary: false, diff: "" });
const hunk = { id: "h1", sha256: "hash" } as GitReviewHunk;
const deferred = <T,>() => {
  let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};
type Client = Pick<GatewayClient, "getTaskBranchReview" | "getTaskBranchReviewDiff" | "applyTaskBranchReviewAction" | "verifyTaskBranchReview">;
const verified = (id = "a"): Awaited<ReturnType<Client["verifyTaskBranchReview"]>> => {
  const verification = { ok: true, head: HEAD, cmd: "pytest -q", output: "passed", at: "2026-10-23T12:00:00Z" };
  return { ok: true, verification, snapshot: { ...snapshot(id), review: {
    accepted_hunks: 0, verification_stale: false, verification,
  } } };
};
function client() {
  return { getTaskBranchReview: vi.fn(async (id: string) => snapshot(id)),
    getTaskBranchReviewDiff: vi.fn(async (id: string, path: string) => diff(id, path)),
    applyTaskBranchReviewAction: vi.fn(async (id: string, input: Parameters<Client["applyTaskBranchReviewAction"]>[1]) => ({ ok: true, action: input.action, snapshot: snapshot(id) })),
    verifyTaskBranchReview: vi.fn<Client["verifyTaskBranchReview"]>(async id => verified(id)) };
}
function harness() {
  const owner = client(); let generation = 0;
  const state = { client: owner as Client | null, view: { task: task(), snapshot: snapshot(), diff: diff(), path: "a.py" } as TaskReviewView,
    loading: false, actionBusy: false, verifying: false, error: "", banners: [] as string[] };
  const writes = new ViewMutations(() => state.client), changed = vi.fn(async () => {}), refresh = vi.fn(async () => {});
  const confirm = vi.fn(async (_message: string) => true);
  const actions = taskBranchReviewActions({ client: () => state.client, view: () => state.view, writes,
    capture: () => { const owner = state.client, version = ++generation; return () => owner === state.client && version === generation; },
    setTask: value => { state.view.task = value; }, setSnapshot: value => { state.view.snapshot = value; },
    setDiff: value => { state.view.diff = value; }, setPath: value => { state.view.path = value; },
    setLoading: value => { state.loading = value; }, setActionBusy: value => { state.actionBusy = value; },
    setVerifying: value => { state.verifying = value; }, setError: value => { state.error = value; }, confirm,
    show: vi.fn(), changed, refreshWorkspace: refresh, banner: text => state.banners.push(text) });
  return { owner, state, writes, actions, changed, refresh, confirm };
}

it("the newest selected file owns the diff and loading indicator", async () => {
  const h = harness(), a = deferred<TaskBranchReviewDiff>(), b = deferred<TaskBranchReviewDiff>();
  h.owner.getTaskBranchReviewDiff.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
  const first = h.actions.loadTaskBranchReviewDiff(task(), "a.py"), second = h.actions.loadTaskBranchReviewDiff(task(), "b.py");
  expect(h.state.view.diff).toBeNull();
  await h.actions.applyTaskBranchAction("accept", hunk);
  expect(h.owner.applyTaskBranchReviewAction).not.toHaveBeenCalled();
  a.resolve(diff()); await first;
  expect(h.state.loading).toBe(true); expect(h.state.view.path).toBe("b.py");
  expect(h.state.view.diff).toBeNull();
  b.resolve(diff("a", "b.py")); await second;
  expect(h.state.view.diff?.path).toBe("b.py"); expect(h.state.loading).toBe(false);
});

it.each(["success", "error"])("ignores an old task's delayed %s after opening another task", async outcome => {
  const h = harness(), slow = deferred<TaskBranchReviewSnapshot>();
  h.owner.getTaskBranchReview.mockReturnValueOnce(slow.promise);
  const old = h.actions.openTaskBranchReview(task("a"));
  await h.actions.openTaskBranchReview(task("b"));
  if (outcome === "success") slow.resolve(snapshot("a")); else slow.reject(new Error("old error"));
  await old;
  expect(h.state.view.snapshot?.task_id).toBe("b"); expect(h.state.view.diff?.task_id).toBe("b");
  expect(h.state.error).toBe("");
});

it("leaving task review invalidates pending diff reads", async () => {
  const h = harness(), slow = deferred<TaskBranchReviewDiff>();
  h.owner.getTaskBranchReviewDiff.mockReturnValueOnce(slow.promise);
  const old = h.actions.loadTaskBranchReviewDiff(task(), "a.py");
  await h.actions.closeTaskBranchReview(); slow.resolve(diff()); await old;
  expect(h.state.view.task).toBeNull(); expect(h.state.view.diff).toBeNull();
  expect(h.state.loading).toBe(false); expect(h.refresh).toHaveBeenCalledTimes(1);
});

it.each(["task", "project", "head"])("changing %s while confirming rejection prevents its write", async changed => {
  const h = harness(), approval = deferred<boolean>(); h.confirm.mockReturnValueOnce(approval.promise);
  const pending = h.actions.applyTaskBranchAction("reject", hunk);
  if (changed === "task") await h.actions.openTaskBranchReview(task("b"));
  else if (changed === "project") { h.state.client = client(); h.actions.reset(); }
  else h.state.view.snapshot = { ...snapshot(), head_oid: "b".repeat(40) };
  approval.resolve(true); await pending;
  expect(h.owner.applyTaskBranchReviewAction).not.toHaveBeenCalled();
});

it("rejects duplicate review mutations and preserves explicit hash/confirmation fields", async () => {
  const h = harness(), approval = deferred<boolean>(); h.confirm.mockReturnValueOnce(approval.promise);
  const a = h.actions.applyTaskBranchAction("reject", hunk), b = h.actions.applyTaskBranchAction("reject", hunk);
  expect(h.confirm).toHaveBeenCalledTimes(1);
  approval.resolve(true); await Promise.all([a, b]);
  expect(h.owner.applyTaskBranchReviewAction).toHaveBeenCalledTimes(1);
  expect(h.owner.applyTaskBranchReviewAction).toHaveBeenCalledWith("a", {
    action: "reject", path: "a.py", hunk_id: "h1", expected_sha256: "hash", confirm: true,
  });
  expect(h.state.banners[0]).toContain("重新验证通过前不能开 PR");
});

it("does not restart an in-flight verification on repeated clicks", async () => {
  const h = harness(), slow = deferred<Awaited<ReturnType<Client["verifyTaskBranchReview"]>>>();
  h.owner.verifyTaskBranchReview.mockReturnValueOnce(slow.promise);
  const a = h.actions.verifyReviewedTaskBranch(), b = h.actions.verifyReviewedTaskBranch();
  expect(h.owner.verifyTaskBranchReview).toHaveBeenCalledTimes(1);
  slow.resolve(verified()); await Promise.all([a, b]);
  expect(h.state.verifying).toBe(false);
  // snapshot.head 是短摘要；应拿 head_oid 与 diff 的完整 head 比较。
  expect(h.state.view.diff).not.toBeNull();
});

it("a verification from before A → B → A cannot overwrite the reopened A", async () => {
  const h = harness(), slow = deferred<Awaited<ReturnType<Client["verifyTaskBranchReview"]>>>();
  h.owner.verifyTaskBranchReview.mockReturnValueOnce(slow.promise);
  const pending = h.actions.verifyReviewedTaskBranch();
  await h.actions.openTaskBranchReview(task("b")); await h.actions.openTaskBranchReview(task("a"));
  slow.resolve({ ok: true, snapshot: { ...snapshot(), head_oid: "old-result" }, verification: null }); await pending;
  expect(h.state.view.snapshot?.head_oid).toBe(HEAD);
  expect(h.changed).not.toHaveBeenCalled(); expect(h.state.banners).toEqual([]);
});

it("an old project's verification cannot clear a new project's spinner", async () => {
  const h = harness(), old = deferred<Awaited<ReturnType<Client["verifyTaskBranchReview"]>>>();
  h.owner.verifyTaskBranchReview.mockReturnValueOnce(old.promise);
  const first = h.actions.verifyReviewedTaskBranch();
  const next = client(), current = deferred<Awaited<ReturnType<Client["verifyTaskBranchReview"]>>>();
  next.verifyTaskBranchReview.mockReturnValueOnce(current.promise);
  h.state.client = next; h.actions.reset();
  const second = h.actions.verifyReviewedTaskBranch(task("b"));
  old.resolve({ ok: true, snapshot: snapshot(), verification: null }); await first;
  expect(h.state.verifying).toBe(true); expect(h.state.view.snapshot).toBeNull();
  current.resolve(verified("b")); await second;
  expect(h.state.view.snapshot?.task_id).toBe("b"); expect(h.state.verifying).toBe(false);
});

it("wrong task identities never become review or verification evidence", async () => {
  const h = harness(); h.owner.getTaskBranchReview.mockResolvedValueOnce(snapshot("wrong"));
  await h.actions.openTaskBranchReview(task());
  expect(h.state.view.snapshot).toBeNull(); expect(h.state.error).toContain("不匹配");
  h.owner.verifyTaskBranchReview.mockResolvedValueOnce({ ok: true, snapshot: snapshot("wrong"), verification: null });
  await h.actions.verifyReviewedTaskBranch();
  expect(h.state.view.snapshot).toBeNull(); expect(h.changed).not.toHaveBeenCalled();
});

it("shares write admission with other review operations", async () => {
  const h = harness(), occupied = h.writes.begin()!;
  await h.actions.applyTaskBranchAction("accept", hunk); await h.actions.verifyReviewedTaskBranch();
  expect(h.owner.applyTaskBranchReviewAction).not.toHaveBeenCalled();
  expect(h.owner.verifyTaskBranchReview).not.toHaveBeenCalled();
  occupied.release(); await h.actions.applyTaskBranchAction("accept", hunk);
  expect(h.owner.applyTaskBranchReviewAction).toHaveBeenCalledTimes(1);
});

it.each(["missing", "wrong head", "stale"])("never promotes %s verification evidence to passed", async invalid => {
  const h = harness(), result = verified();
  if (invalid === "missing") result.verification = null;
  else if (invalid === "wrong head") result.verification = { ...result.verification!, head: "wrong" };
  else result.snapshot.review.verification_stale = true;
  h.owner.verifyTaskBranchReview.mockResolvedValueOnce(result);
  await h.actions.verifyReviewedTaskBranch();
  expect(h.state.error).toContain("不能确认通过");
  expect(h.changed).not.toHaveBeenCalled();
  expect(h.state.banners.some(text => text.includes("可以继续交付"))).toBe(false);
});
