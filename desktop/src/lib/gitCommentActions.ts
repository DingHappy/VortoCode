import type { Dispatch, SetStateAction } from "react";
import type { GatewayClient } from "../gateway";
import type { GitReviewComment, GitReviewScope, PendingGitComment } from "../types";
import { errorText } from "./errorText";
import { ViewMutations } from "./viewRequests";

export function gitCommentActions(deps: {
  client: () => Pick<GatewayClient, "createGitReviewComment" | "updateGitReviewComment" | "deleteGitReviewComment"> | null;
  context: () => string;
  pending: () => PendingGitComment | null;
  draft: () => string;
  comments: () => GitReviewComment[];
  writes: ViewMutations;
  setComments: Dispatch<SetStateAction<GitReviewComment[]>>;
  clearDraft: (pending: PendingGitComment, expected: string) => void;
  setBusy: (busy: boolean) => void;
  banner: (message: string) => void;
  refresh: (path: string, scope: GitReviewScope) => Promise<void>;
}) {
  async function run<T>(perform: (client: NonNullable<ReturnType<typeof deps.client>>) => Promise<T>,
    publish: (result: T) => void, fallback: string, failed?: () => Promise<void>) {
    const client = deps.client(); if (!client) return;
    const ticket = deps.writes.begin();
    if (!ticket) { deps.banner("另一项审查操作尚未结束，请稍后重试"); return; }
    const context = deps.context(), current = () => ticket.current() && context === deps.context();
    deps.setBusy(true);
    try { const result = await perform(client); if (current()) publish(result); }
    catch (error) { if (current()) { deps.banner(errorText(error, fallback)); if (failed) await failed(); } }
    finally { const visible = ticket.current(); ticket.release(); if (visible) deps.setBusy(false); }
  }
  const addGitComment = () => {
    const target = deps.pending(), draft = deps.draft(), body = draft.trim();
    if (!target || !body) return Promise.resolve();
    const pending = { ...target };
    return run(client => client.createGitReviewComment({ path: pending.path, scope: pending.scope,
      hunk_id: pending.hunkId, expected_sha256: pending.hunkSha256, line: pending.line, side: pending.side, body }),
    created => {
      deps.setComments(previous => [created, ...previous.filter(item => item.id !== created.id)]);
      deps.clearDraft(target, draft);
    }, "保存行级评论失败", () => deps.refresh(pending.path, pending.scope));
  };
  const present = (comment: GitReviewComment) => deps.comments().some(item => item.id === comment.id
    && item.path === comment.path && item.hunkSha256 === comment.hunkSha256);
  const updateGitCommentStatus = (comment: GitReviewComment, status: GitReviewComment["status"]) => {
    if (!present(comment)) return Promise.resolve();
    return run(client => client.updateGitReviewComment(comment.id, status), updated => {
      deps.setComments(previous => previous.map(item => item.id === updated.id ? updated : item));
    }, "更新审查评论失败");
  };
  const deleteGitComment = (comment: GitReviewComment) => {
    if (!present(comment)) return Promise.resolve();
    return run(client => client.deleteGitReviewComment(comment.id), () => {
      deps.setComments(previous => previous.filter(item => item.id !== comment.id));
    }, "删除审查评论失败");
  };
  const reset = () => { deps.writes.invalidate(); deps.setBusy(false); };
  return { addGitComment, updateGitCommentStatus, deleteGitComment, reset };
}
