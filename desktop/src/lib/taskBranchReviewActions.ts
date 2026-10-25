import type { GatewayClient } from "../gateway";
import type { GitReviewHunk, TaskBranchReviewDiff, TaskBranchReviewSnapshot, TaskItem } from "../types";
import { errorText } from "./errorText";
import { ViewMutations } from "./viewRequests";

export interface TaskReviewView {
  task: TaskItem | null;
  snapshot: TaskBranchReviewSnapshot | null;
  diff: TaskBranchReviewDiff | null;
  path: string;
}
type Client = Pick<GatewayClient, "getTaskBranchReview" | "getTaskBranchReviewDiff" | "applyTaskBranchReviewAction" | "verifyTaskBranchReview">;
export function taskBranchReviewActions(deps: {
  client: () => Client | null;
  view: () => TaskReviewView;
  capture: () => () => boolean;
  writes: ViewMutations;
  setTask: (task: TaskItem | null) => void;
  setSnapshot: (snapshot: TaskBranchReviewSnapshot | null) => void;
  setDiff: (diff: TaskBranchReviewDiff | null) => void;
  setPath: (path: string) => void;
  setLoading: (loading: boolean) => void;
  setActionBusy: (busy: boolean) => void;
  setVerifying: (busy: boolean) => void;
  setError: (error: string) => void;
  confirm: (message: string) => Promise<boolean>;
  show: () => void;
  changed: () => Promise<void>;
  refreshWorkspace: () => Promise<void>;
  banner: (message: string) => void;
}) {
  let selection = 0;
  const belongs = (task: TaskItem) => deps.view().task?.id === task.id;
  const loadTaskBranchReviewDiff = async (task: TaskItem, path: string) => {
    const client = deps.client(); if (!client || !path || !belongs(task)) return;
    selection++;
    const request = deps.capture(), current = () => request() && belongs(task);
    deps.setLoading(true); deps.setError(""); deps.setPath(path); deps.setDiff(null);
    try {
      const diff = await client.getTaskBranchReviewDiff(task.id, path);
      if (diff.task_id !== task.id || diff.path !== path) throw new Error("运行端返回的任务差异不匹配");
      if (current()) deps.setDiff(diff);
    } catch (error) { if (current()) { deps.setDiff(null); deps.setError(errorText(error, "读取任务分支 diff 失败")); } }
    finally { if (current()) deps.setLoading(false); }
  };
  const openTaskBranchReview = async (task: TaskItem, preferredPath = "") => {
    const client = deps.client();
    if (!client || (!task.branch && !task.plan?.branch)) { deps.banner("这个任务还没有可审查的分支"); return; }
    selection++;
    const request = deps.capture(), current = () => request() && belongs(task);
    deps.setTask(task); deps.setSnapshot(null); deps.setDiff(null); deps.setPath(""); deps.setVerifying(false);
    deps.setError(""); deps.show(); deps.setLoading(true);
    try {
      const snapshot = await client.getTaskBranchReview(task.id);
      if (!current()) return;
      if (snapshot.task_id !== task.id) throw new Error("运行端返回的审查任务不匹配");
      deps.setSnapshot(snapshot);
      const selected = snapshot.files.find(file => file.path === preferredPath) ?? snapshot.files[0];
      if (!selected) return;
      deps.setPath(selected.path);
      const diff = await client.getTaskBranchReviewDiff(task.id, selected.path);
      if (diff.task_id !== task.id || diff.path !== selected.path) throw new Error("运行端返回的任务差异不匹配");
      if (current()) deps.setDiff(diff);
    } catch (error) { if (current()) deps.setError(errorText(error, "读取任务分支审查失败")); }
    finally { if (current()) deps.setLoading(false); }
  };
  const applyTaskBranchAction = async (action: "accept" | "reject", hunk: GitReviewHunk) => {
    const client = deps.client(), latest = deps.view();
    const view = { ...latest, snapshot: latest.snapshot ? { ...latest.snapshot } : null,
      diff: latest.diff ? { ...latest.diff } : null };
    const task = view.task, snapshot = view.snapshot, diff = view.diff;
    if (!client || !task || !snapshot || !diff) return;
    const ticket = deps.writes.begin();
    if (!ticket) { deps.setError("另一项审查操作尚未结束，请稍后重试"); return; }
    const selectedVersion = selection;
    const current = () => ticket.current() && selectedVersion === selection && belongs(task) && deps.view().snapshot?.branch === snapshot.branch
      && deps.view().path === view.path;
    let errorCurrent = current;
    deps.setActionBusy(true); deps.setError("");
    try {
      if (action === "reject" && await deps.confirm(
        `确定撤销任务分支 ${snapshot.branch} 中 ${diff.path} 的这个改动块吗？\n\n将创建一条审查提交，旧测试证据会立即失效。`) !== true) return;
      if (!current()) return;
      if (deps.view().snapshot?.head_oid !== snapshot.head_oid || deps.view().diff?.baseline !== diff.baseline) {
        deps.setError("任务分支或差异已变化，请重新核对后操作"); return;
      }
      const result = await client.applyTaskBranchReviewAction(task.id, { action, path: diff.path,
        hunk_id: hunk.id, expected_sha256: hunk.sha256, confirm: action === "reject" });
      if (!current()) return;
      if (result.snapshot.task_id !== task.id) throw new Error("运行端返回的审查任务不匹配");
      const request = deps.capture();
      deps.setSnapshot(result.snapshot); deps.setDiff(null);
      const selected = result.snapshot.files.find(file => file.path === diff.path) ?? result.snapshot.files[0];
      deps.setPath(selected?.path ?? "");
      const stillCurrent = () => ticket.current() && selectedVersion === selection && request() && belongs(task);
      errorCurrent = stillCurrent;
      if (selected) {
        const next = await client.getTaskBranchReviewDiff(task.id, selected.path);
        if (next.task_id !== task.id || next.path !== selected.path) throw new Error("运行端返回的任务差异不匹配");
        if (!stillCurrent()) return;
        deps.setDiff(next);
      }
      if (!stillCurrent()) return;
      await deps.changed();
      if (stillCurrent()) deps.banner(action === "accept" ? "已记录稳定 hunk 接受证据"
        : "已在任务分支创建撤销提交；重新验证通过前不能开 PR");
    } catch (error) { if (errorCurrent()) deps.setError(errorText(error, "任务分支审查操作失败")); }
    finally { const visible = ticket.current(); ticket.release(); if (visible) deps.setActionBusy(false); }
  };
  const verifyReviewedTaskBranch = async (targetTask: TaskItem | null = deps.view().task) => {
    const client = deps.client(); if (!client || !targetTask) return;
    const ticket = deps.writes.begin();
    if (!ticket) { deps.setError("另一项审查操作尚未结束，请稍后重试"); return; }
    deps.capture();
    if (!belongs(targetTask)) { selection++; deps.setSnapshot(null); deps.setDiff(null); deps.setPath(""); }
    const selectedVersion = selection;
    deps.setTask(targetTask); deps.setVerifying(true); deps.setError("");
    deps.setLoading(false);
    const current = () => ticket.current() && selectedVersion === selection && belongs(targetTask);
    try {
      const result = await client.verifyTaskBranchReview(targetTask.id);
      if (!current()) return;
      if (result.snapshot.task_id !== targetTask.id) throw new Error("运行端返回的验证任务不匹配");
      if (result.ok && (result.verification?.ok !== true || result.verification.head !== result.snapshot.head_oid
          || result.snapshot.review.verification_stale || result.snapshot.review.verification?.ok !== true
          || result.snapshot.review.verification.head !== result.snapshot.head_oid)) {
        throw new Error("验证证据未绑定当前提交，不能确认通过");
      }
      deps.capture();
      deps.setSnapshot(result.snapshot);
      if (deps.view().diff?.head !== result.snapshot.head_oid) deps.setDiff(null);
      await deps.changed();
      if (current()) deps.banner(result.ok ? "任务分支已在隔离 worktree 重新验证通过，可以继续交付"
        : "重新验证未通过，PR 闸门保持关闭");
    } catch (error) { if (current()) { const text = errorText(error, "任务分支重新验证失败"); deps.setError(text); deps.banner(text); } }
    finally { const visible = ticket.current() && belongs(targetTask); ticket.release(); if (visible) deps.setVerifying(false); }
  };
  const leave = () => {
    selection++;
    deps.capture(); deps.setTask(null); deps.setSnapshot(null); deps.setDiff(null); deps.setPath("");
    deps.setLoading(false); deps.setVerifying(false);
  };
  const closeTaskBranchReview = async () => { leave(); await deps.refreshWorkspace(); };
  const reset = () => { deps.writes.invalidate(); leave(); deps.setActionBusy(false); };
  return { loadTaskBranchReviewDiff, openTaskBranchReview, closeTaskBranchReview, applyTaskBranchAction,
    verifyReviewedTaskBranch, leave, reset };
}
