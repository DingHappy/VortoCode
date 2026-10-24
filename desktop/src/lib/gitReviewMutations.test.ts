import { expect, it, vi } from "vitest";
import type { GatewayClient } from "../gateway";
import type { GitReviewCommitResult, GitReviewHunk, GitReviewSnapshot } from "../types";
import { gitReviewMutations, type GitMutationView } from "./gitReviewMutations";

const snapshot = (branch = "feature", head = "head-1"): GitReviewSnapshot => ({
  ok: true, root: "/repo-a", branch, head, files: [], truncated: false,
});
const commit = (): GitReviewCommitResult => ({ ok: true, sha: "head-2", output: "", snapshot: snapshot("feature", "head-2") });
const deferred = <T,>() => {
  let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};
function client() {
  return { applyGitReviewAction: vi.fn(async () => ({ ok: true, snapshot: snapshot() })),
    commitGitReview: vi.fn(async () => commit()),
    openGitReviewPr: vi.fn(async () => ({ ok: true, pushed: true, url: "https://example.com/pr/1", branch: "feature", base: "main" })) };
}
function harness() {
  const owner = client();
  const state = { client: owner as Pick<GatewayClient, "applyGitReviewAction" | "commitGitReview" | "openGitReviewPr"> | null,
    view: { snapshot: snapshot(), revision: "revision-1", commitMessage: "fix", prTitle: "change", prBase: "main",
      commentDraft: "", pendingComment: null, scope: "working" } as GitMutationView,
    actionBusy: false, deliveryBusy: false, banners: [] as string[] };
  const confirm = vi.fn(async (_text: string) => true), refresh = vi.fn(async () => {}), refreshPr = vi.fn(async () => {});
  const open = vi.fn(async (_url: string) => {}), clearComment = vi.fn(), committed = vi.fn();
  const actions = gitReviewMutations({ client: () => state.client, view: () => state.view, confirm,
    busy: (kind, value) => { if (kind === "action") state.actionBusy = value; else state.deliveryBusy = value; },
    setSnapshot: value => { state.view = { ...state.view, snapshot: value }; },
    clearComment, committed, refreshGitReview: refresh, refreshPrDelivery: refreshPr,
    banner: text => state.banners.push(text), openUrl: open });
  return { state, owner, actions, confirm, refresh, refreshPr, open, committed, clearComment };
}
type Harness = ReturnType<typeof harness>;
const operations = {
  revert: (h: Harness) => h.actions.applyGitAction("revert", "a.py"),
  commit: (h: Harness) => h.actions.commitGitReview(),
  pr: (h: Harness) => h.actions.openGitReviewPr(),
};

it.each(Object.keys(operations) as Array<keyof typeof operations>)("%s never writes into a project selected during confirmation", async operation => {
  const h = harness(), approval = deferred<boolean>();
  if (operation === "commit") h.state.view.snapshot = snapshot("main");
  h.confirm.mockReturnValueOnce(approval.promise);
  const pending = operations[operation](h);
  const next = client(); h.state.client = next; h.actions.reset();
  h.state.view = { ...h.state.view, snapshot: { ...snapshot(), root: "/repo-b" } };
  approval.resolve(true); await pending;
  for (const target of [h.owner, next]) {
    expect(target.applyGitReviewAction).not.toHaveBeenCalled();
    expect(target.commitGitReview).not.toHaveBeenCalled();
    expect(target.openGitReviewPr).not.toHaveBeenCalled();
  }
  expect(h.state.banners).toEqual([]);
});

it.each(["branch", "head", "revision"])("a changed %s invalidates approval in the same project", async changed => {
  const h = harness(), approval = deferred<boolean>();
  h.confirm.mockReturnValueOnce(approval.promise);
  const pending = h.actions.openGitReviewPr();
  if (changed === "revision") h.state.view.revision = "revision-2";
  else h.state.view.snapshot = { ...snapshot(), [changed]: "changed" };
  approval.resolve(true); await pending;
  expect(h.owner.openGitReviewPr).not.toHaveBeenCalled();
  expect(h.state.banners[0]).toContain("重新核对");
  expect(h.state.deliveryBusy).toBe(false);
});

it.each(Object.keys(operations) as Array<keyof typeof operations>)("repeat %s clicks share one admission while approval is pending", async operation => {
  const h = harness(), approval = deferred<boolean>();
  if (operation === "commit") h.state.view.snapshot = snapshot("main");
  h.confirm.mockReturnValueOnce(approval.promise);
  const a = operations[operation](h), b = operations[operation](h);
  expect(h.confirm).toHaveBeenCalledTimes(1);
  approval.resolve(true); await Promise.all([a, b]);
  const total = h.owner.applyGitReviewAction.mock.calls.length + h.owner.commitGitReview.mock.calls.length
    + h.owner.openGitReviewPr.mock.calls.length;
  expect(total).toBe(1);
});

it("serializes Git writes before React can render busy state", async () => {
  const h = harness(), slow = deferred<GitReviewCommitResult>();
  h.owner.commitGitReview.mockReturnValueOnce(slow.promise);
  const first = h.actions.commitGitReview();
  await h.actions.applyGitAction("stage", "a.py");
  expect(h.owner.applyGitReviewAction).not.toHaveBeenCalled();
  expect(h.owner.commitGitReview).toHaveBeenCalledTimes(1);
  slow.resolve(commit()); await first;
  expect(h.state.deliveryBusy).toBe(false);
});

it.each(["success", "error"])("a late %s cannot update the new project or clear its busy flag", async outcome => {
  const h = harness(), old = deferred<GitReviewCommitResult>(), current = deferred<GitReviewCommitResult>();
  h.owner.commitGitReview.mockReturnValueOnce(old.promise);
  const first = h.actions.commitGitReview();
  const next = client(); next.commitGitReview.mockReturnValueOnce(current.promise);
  h.state.client = next; h.actions.reset();
  const second = h.actions.commitGitReview();
  if (outcome === "success") old.resolve(commit()); else old.reject(new Error("old project error"));
  await first;
  expect(h.state.deliveryBusy).toBe(true);
  expect(h.state.banners).toEqual([]); expect(h.committed).not.toHaveBeenCalled();
  current.resolve(commit()); await second;
  expect(h.state.deliveryBusy).toBe(false);
  expect(h.committed).toHaveBeenCalledTimes(1);
});

it("cancel releases admission without running or replaying a write", async () => {
  const h = harness(); h.confirm.mockResolvedValueOnce(false);
  await h.actions.openGitReviewPr();
  expect(h.owner.openGitReviewPr).not.toHaveBeenCalled();
  await h.actions.openGitReviewPr();
  expect(h.owner.openGitReviewPr).toHaveBeenCalledTimes(1);
});

it("keeps an in-flight write reserved across reset until its result is known", async () => {
  const h = harness(), slow = deferred<GitReviewCommitResult>();
  h.owner.commitGitReview.mockReturnValueOnce(slow.promise);
  const first = h.actions.commitGitReview(); h.actions.reset();
  await h.actions.commitGitReview();
  expect(h.owner.commitGitReview).toHaveBeenCalledTimes(1);
  slow.resolve(commit()); await first;
  expect(h.committed).not.toHaveBeenCalled();
  await h.actions.commitGitReview();
  expect(h.owner.commitGitReview).toHaveBeenCalledTimes(2);
});

it("preserves the hunk hash and explicit destructive confirmation", async () => {
  const h = harness(), hunk = { id: "h1", sha256: "digest" } as GitReviewHunk;
  await h.actions.applyGitAction("revert", "a.py", hunk);
  expect(h.owner.applyGitReviewAction).toHaveBeenCalledWith({ action: "revert", path: "a.py", scope: "working",
    hunk_id: "h1", expected_sha256: "digest", confirm: true });
  expect(h.clearComment).toHaveBeenCalledTimes(1);
});

it("does not open a prior project's PR link after switching during refresh", async () => {
  const h = harness(), slow = deferred<void>(); h.refreshPr.mockReturnValueOnce(slow.promise);
  const pending = h.actions.openGitReviewPr();
  await vi.waitFor(() => expect(h.refreshPr).toHaveBeenCalledTimes(1));
  h.state.client = client(); h.actions.reset();
  slow.resolve(); await pending;
  expect(h.open).not.toHaveBeenCalled();
});
