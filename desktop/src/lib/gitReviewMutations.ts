import type { GatewayClient } from "../gateway";
import type { GitReviewAction, GitReviewHunk, GitReviewScope, GitReviewSnapshot, PendingGitComment } from "../types";
import { isProtectedBranch } from "./branches";
import { errorText } from "./errorText";
import { ViewMutations } from "./viewRequests";

export interface GitMutationView {
  snapshot: GitReviewSnapshot | null;
  revision: string;
  commitMessage: string;
  prTitle: string;
  prBase: string;
  commentDraft: string;
  pendingComment: PendingGitComment | null;
  scope: GitReviewScope;
}
type Client = Pick<GatewayClient, "applyGitReviewAction" | "commitGitReview" | "openGitReviewPr">;
export function gitReviewMutations(deps: {
  writes?: ViewMutations;
  client: () => Client | null;
  view: () => GitMutationView;
  confirm: (message: string) => Promise<boolean>;
  busy: (kind: "action" | "delivery", value: boolean) => void;
  setSnapshot: (snapshot: GitReviewSnapshot) => void;
  clearComment: (view: GitMutationView) => void;
  committed: (message: string) => void;
  refreshGitReview: (path?: string, scope?: GitReviewScope) => Promise<void>;
  refreshPrDelivery: () => Promise<void>;
  banner: (text: string) => void;
  openUrl: (url: string) => Promise<unknown>;
}) {
  const writes = deps.writes ?? new ViewMutations(deps.client);
  const identity = (view: GitMutationView) => JSON.stringify([
    view.snapshot?.root, view.snapshot?.branch, view.snapshot?.head, view.revision,
  ]);
  async function run<T>(kind: "action" | "delivery", prompt: (view: GitMutationView) => string | null,
    perform: (client: Client, view: GitMutationView) => Promise<T>,
    success: (result: T, view: GitMutationView, current: () => boolean) => Promise<void>,
    error: string, failure?: (view: GitMutationView) => Promise<void>) {
    const client = deps.client(), latest = deps.view();
    const view = { ...latest, snapshot: latest.snapshot ? { ...latest.snapshot } : null };
    if (!client || !view.snapshot?.ok || !view.snapshot.branch) return;
    const ticket = writes.begin();
    if (!ticket) { deps.banner("另一项审查操作尚未结束，请稍后重试"); return; }
    const expected = identity(view);
    const current = () => ticket.current() && view.snapshot?.root === deps.view().snapshot?.root
      && view.snapshot?.branch === deps.view().snapshot?.branch;
    deps.busy(kind, true);
    try {
      const message = prompt(view);
      if (message !== null && await deps.confirm(message) !== true) return;
      if (!ticket.current()) return;
      if (expected !== identity(deps.view())) {
        deps.banner("工作区或审查状态已变化，请重新核对后操作");
        return;
      }
      // 只使用发起时的客户端；确认期间切换项目不能重定向写入。
      const result = await perform(client, view);
      if (current()) await success(result, view, current);
    } catch (cause) {
      if (current()) { deps.banner(errorText(cause, error)); if (failure) await failure(view); }
    } finally {
      const visible = ticket.current();
      ticket.release();
      if (visible) deps.busy(kind, false);
    }
  }
  const applyGitAction = (action: GitReviewAction, path: string, hunk?: GitReviewHunk) => run("action",
    () => action === "revert" ? `撤销 ${hunk ? `${path} 的 ${hunk.id}` : path} 的本地修改？这会丢弃对应内容，且不可从 VortoCode 恢复。` : null,
    client => client.applyGitReviewAction({ action, path, scope: action === "unstage" ? "staged" : "working",
      hunk_id: hunk?.id, expected_sha256: hunk?.sha256, confirm: action === "revert" }),
    async (result, view, current) => {
      deps.setSnapshot(result.snapshot); deps.clearComment(view);
      await deps.refreshGitReview(path, action === "unstage" ? "staged" : "working");
      if (current()) deps.banner(action === "stage" ? "Git 改动已暂存" : action === "unstage" ? "Git 改动已取消暂存" : "本地改动已撤销");
    }, "Git 操作失败", view => deps.refreshGitReview(path, view.scope));
  const commitGitReview = () => {
    if (!deps.view().commitMessage.trim()) return Promise.resolve();
    return run("delivery", view => isProtectedBranch(view.snapshot?.branch)
      ? `当前在受保护分支 ${view.snapshot?.branch} 上。\n\n直接提交到这里之后就不能从它开 PR 了（需要先切到功能分支）。确认要直接提交到 ${view.snapshot?.branch} 吗？` : null,
    (client, view) => client.commitGitReview(view.commitMessage.trim(), isProtectedBranch(view.snapshot?.branch)),
    async (result, view, current) => {
      deps.setSnapshot(result.snapshot); deps.committed(view.commitMessage);
      await deps.refreshGitReview();
      if (current()) deps.banner(`已提交审查范围 · ${result.sha}`);
    }, "Git 提交失败");
  };
  const openGitReviewPr = () => {
    if (!deps.view().prTitle.trim() || !deps.view().prBase.trim()) return Promise.resolve();
    return run("delivery", view => `把当前分支 ${view.snapshot?.branch} push 到 origin，并向 ${view.prBase.trim()} 创建 Draft PR？`,
      (client, view) => client.openGitReviewPr({ title: view.prTitle.trim(), base: view.prBase.trim(),
        body: "由 VortoCode Desktop 在逐文件、逐 hunk 审查后创建。", confirm: true }),
      async (result, _view, current) => {
        deps.banner(`Draft PR 已创建：${result.url}`);
        await deps.refreshPrDelivery();
        if (current() && result.url) await deps.openUrl(result.url);
      }, "创建 Draft PR 失败");
  };
  const invalidate = () => writes.invalidate();
  const reset = () => { invalidate(); deps.busy("action", false); deps.busy("delivery", false); };
  return { applyGitAction, commitGitReview, openGitReviewPr, invalidate, reset };
}
