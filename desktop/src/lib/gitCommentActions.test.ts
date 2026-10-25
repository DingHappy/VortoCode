import { expect, it, vi } from "vitest";
import type { GatewayClient } from "../gateway";
import type { GitReviewComment, PendingGitComment } from "../types";
import { gitCommentActions } from "./gitCommentActions";
import { ViewMutations } from "./viewRequests";

const pending: PendingGitComment = { path: "a.py", scope: "working", hunkId: "h1", hunkSha256: "digest", line: 1, side: "new" };
const comment: GitReviewComment = { id: "c1", ...pending, body: "comment", status: "open", created: "now", updated: "now" };
const deferred = <T,>() => {
  let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};
type Client = Pick<GatewayClient, "createGitReviewComment" | "updateGitReviewComment" | "deleteGitReviewComment">;
function client() {
  return { createGitReviewComment: vi.fn(async (_input: Parameters<Client["createGitReviewComment"]>[0]) => comment),
    updateGitReviewComment: vi.fn(async (_id: string, status: GitReviewComment["status"]) => ({ ...comment, status })),
    deleteGitReviewComment: vi.fn(async (_id: string) => {}) };
}
function harness() {
  const owner = client();
  const state = { client: owner as Client | null, context: "project-a:head", pending: pending as PendingGitComment | null,
    draft: "comment", comments: [] as GitReviewComment[], busy: false, banners: [] as string[] };
  const writes = new ViewMutations(() => state.client), refresh = vi.fn(async () => {});
  const actions = gitCommentActions({ client: () => state.client, context: () => state.context,
    pending: () => state.pending, draft: () => state.draft, comments: () => state.comments, writes,
    setComments: value => { state.comments = typeof value === "function" ? value(state.comments) : value; },
    clearDraft: (target, expected) => { if (state.pending === target && state.draft === expected) { state.pending = null; state.draft = ""; } },
    setBusy: value => { state.busy = value; }, banner: text => state.banners.push(text), refresh });
  return { owner, state, writes, actions, refresh };
}

it("rapid repeated saves create one comment and preserve the hunk hash", async () => {
  const h = harness(), slow = deferred<GitReviewComment>(); h.owner.createGitReviewComment.mockReturnValueOnce(slow.promise);
  const first = h.actions.addGitComment(), second = h.actions.addGitComment();
  expect(h.owner.createGitReviewComment).toHaveBeenCalledTimes(1);
  expect(h.owner.createGitReviewComment).toHaveBeenCalledWith({ path: "a.py", scope: "working", hunk_id: "h1",
    expected_sha256: "digest", line: 1, side: "new", body: "comment" });
  slow.resolve(comment); await Promise.all([first, second]);
  expect(h.state.comments).toEqual([comment]); expect(h.state.busy).toBe(false);
});

it("saving an old comment never clears a new anchor or draft", async () => {
  const h = harness(), slow = deferred<GitReviewComment>(); h.owner.createGitReviewComment.mockReturnValueOnce(slow.promise);
  const writing = h.actions.addGitComment();
  h.state.pending = { ...pending, hunkId: "h2" }; h.state.draft = "new draft";
  slow.resolve(comment); await writing;
  expect(h.state.pending?.hunkId).toBe("h2"); expect(h.state.draft).toBe("new draft");
  expect(h.state.comments).toEqual([comment]);
});

it.each(["success", "error"])("does not publish late %s or clear a new project's busy flag", async outcome => {
  const h = harness(), old = deferred<GitReviewComment>(), current = deferred<GitReviewComment>();
  h.owner.createGitReviewComment.mockReturnValueOnce(old.promise);
  const first = h.actions.addGitComment();
  const next = client(); next.createGitReviewComment.mockReturnValueOnce(current.promise);
  h.state.client = next; h.state.context = "project-b:head"; h.actions.reset();
  const second = h.actions.addGitComment();
  if (outcome === "success") old.resolve(comment); else old.reject(new Error("old error"));
  await first;
  expect(h.state.comments).toEqual([]); expect(h.state.banners).toEqual([]); expect(h.state.busy).toBe(true);
  current.resolve({ ...comment, id: "new" }); await second;
  expect(h.state.comments[0].id).toBe("new"); expect(h.state.busy).toBe(false);
});

it("discards a result after changing branch in the same project", async () => {
  const h = harness(), slow = deferred<GitReviewComment>(); h.owner.createGitReviewComment.mockReturnValueOnce(slow.promise);
  const writing = h.actions.addGitComment(); h.state.context = "project-a:new-head";
  slow.resolve(comment); await writing;
  expect(h.state.comments).toEqual([]); expect(h.state.draft).toBe("comment");
});

it("updates and deletes only comments present in the current view", async () => {
  const h = harness();
  await h.actions.updateGitCommentStatus(comment, "resolved"); await h.actions.deleteGitComment(comment);
  expect(h.owner.updateGitReviewComment).not.toHaveBeenCalled(); expect(h.owner.deleteGitReviewComment).not.toHaveBeenCalled();
  h.state.comments = [comment];
  await h.actions.updateGitCommentStatus(comment, "resolved");
  expect(h.state.comments[0].status).toBe("resolved");
  await h.actions.deleteGitComment(h.state.comments[0]); expect(h.state.comments).toEqual([]);
});

it("shares write admission with Git operations and releases failures without automatic retry", async () => {
  const h = harness(), busy = h.writes.begin()!;
  await h.actions.addGitComment(); expect(h.owner.createGitReviewComment).not.toHaveBeenCalled();
  busy.release(); h.owner.createGitReviewComment.mockRejectedValueOnce(new Error("lost"));
  await h.actions.addGitComment();
  expect(h.owner.createGitReviewComment).toHaveBeenCalledTimes(1); expect(h.state.draft).toBe("comment");
  await h.actions.addGitComment(); expect(h.owner.createGitReviewComment).toHaveBeenCalledTimes(2);
});
