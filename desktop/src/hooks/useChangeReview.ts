// 「变更」面板域 hook：工作区 Git 审查、任务分支审查、行级评论、PR/CI 交付状态
//（沿用 useJournal 的 hook 模式与三条红线）。
//
// Git 审查与任务分支审查共用一个请求代数（gitReviewRefreshGenerationRef）和同一块面板，
// 所以作为一个域整体迁移。函数体是从 App 原样搬来的，只替换了三个跨域触点：
//   - 结果提示 → onBanner
//   - 打开「变更」面板 → onShowChanges
//   - 任务分支操作后刷新任务与 worktree → onTaskBranchChanged
// 留在 App 的：sendGitCommentsToAgent / sendPrFeedbackToAgent（走 sendPrompt 发给 Agent，属对话域），
// 以及协议事件 git_review_changed 对 setGitReviewRevision 的写入（dispatcher 仍只在 App，红线 1）。
//
// Git 读取继续用 useCallback；PR/CI 读取由恒等稳定的 prDeliveryActions 提供，
// 使用最新 client 引用及请求代次隔离切换、重置和并发刷新。
import { openUrl } from "@tauri-apps/plugin-opener";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { RefObject, SetStateAction } from "react";

import type { GatewayClient } from "../gateway";
import { errorText } from "../lib/errorText";
import { confirmAction } from "../lib/confirm";
import { prDeliveryActions } from "../lib/prDeliveryActions";
import { gitReviewMutations, type GitMutationView } from "../lib/gitReviewMutations";
import { taskBranchReviewActions, type TaskReviewView } from "../lib/taskBranchReviewActions";
import { gitCommentActions } from "../lib/gitCommentActions";
import { ViewMutations } from "../lib/viewRequests";
import type {
  GitReviewComment,
  GitReviewDiff,
  GitReviewFile,
  GitReviewHunk,
  GitReviewScope,
  GitReviewSnapshot,
  PendingGitComment,
  PrDeliveryCheckLog,
  PrDeliverySnapshot,
  TaskBranchReviewDiff,
  TaskBranchReviewSnapshot,
  TaskItem,
} from "../types";

export function useChangeReview(
  clientRef: RefObject<GatewayClient | null>,
  onBanner: (text: string) => void,
  onShowChanges: () => void,
  onTaskBranchChanged: () => Promise<void>,
) {
  const [gitReview, setGitReview] = useState<GitReviewSnapshot | null>(null);
  const [gitReviewDiff, setGitReviewDiff] = useState<GitReviewDiff | null>(null);
  const [gitSelectedPath, setGitSelectedPath] = useState("");
  const [gitReviewScope, setGitReviewScope] = useState<GitReviewScope>("working");
  const [gitReviewLoading, setGitReviewLoading] = useState(false);
  const [gitReviewError, setGitReviewError] = useState("");
  const [gitReviewRevision, setGitReviewRevision] = useState<{
    baseline: string;
    files: number;
    reason: string;
    receivedAt: number;
  } | null>(null);
  const [gitActionBusy, setGitActionBusy] = useState(false);
  const [taskReviewTask, setTaskReviewTask] = useState<TaskItem | null>(null);
  const [taskBranchReview, setTaskBranchReview] = useState<TaskBranchReviewSnapshot | null>(null);
  const [taskBranchDiff, setTaskBranchDiff] = useState<TaskBranchReviewDiff | null>(null);
  const [taskBranchSelectedPath, setTaskBranchSelectedPath] = useState("");
  const [taskReviewVerifying, setTaskReviewVerifying] = useState(false);
  const [gitComments, setGitCommentsState] = useState<GitReviewComment[]>([]);
  const commentVersionRef = useRef(0);
  const setGitComments = useCallback((value: SetStateAction<GitReviewComment[]>) => {
    commentVersionRef.current++;
    setGitCommentsState(value);
  }, []);
  const [pendingGitComment, setPendingGitComment] = useState<PendingGitComment | null>(null);
  const [gitCommentDraft, setGitCommentDraft] = useState("");
  const [gitCommentSaving, setGitCommentSaving] = useState(false);
  const [gitCommitMessage, setGitCommitMessage] = useState("");
  const [gitPrTitle, setGitPrTitle] = useState("");
  const [gitPrBase, setGitPrBase] = useState("main");
  const [gitDeliveryBusy, setGitDeliveryBusy] = useState(false);
  const [prDelivery, setPrDelivery] = useState<PrDeliverySnapshot | null>(null);
  const [prDeliveryLoading, setPrDeliveryLoading] = useState(false);
  const [prCheckLogs, setPrCheckLogs] = useState<Record<string, PrDeliveryCheckLog>>({});
  const prLatest = useRef({ clientRef, onBanner, prDelivery });
  prLatest.current = { clientRef, onBanner, prDelivery };
  const reviewWritesRef = useRef<ViewMutations | null>(null);
  if (!reviewWritesRef.current) reviewWritesRef.current = new ViewMutations(() => prLatest.current.clientRef.current);
  const reviewWrites = reviewWritesRef.current;
  const taskActionsRef = useRef<ReturnType<typeof taskBranchReviewActions> | null>(null);
  const prActionsRef = useRef<ReturnType<typeof prDeliveryActions> | null>(null);
  if (!prActionsRef.current) prActionsRef.current = prDeliveryActions({
    client: () => prLatest.current.clientRef.current,
    snapshot: () => prLatest.current.prDelivery,
    setSnapshot: snapshot => { prLatest.current.prDelivery = snapshot; setPrDelivery(snapshot); },
    setLogs: setPrCheckLogs, setLoading: setPrDeliveryLoading,
    banner: text => prLatest.current.onBanner(text),
  });
  const prActions = prActionsRef.current;
  const { refreshPrDelivery, loadPrCheckLog } = prActions;
  useEffect(() => () => prActions.invalidate(), [prActions]);
  const gitReviewRefreshGenerationRef = useRef(0);
  const openGitComments = useMemo(
    () => gitComments.filter((comment) => comment.status === "open"),
    [gitComments],
  );
  const sentGitComments = useMemo(
    () => gitComments.filter((comment) => comment.status === "sent"),
    [gitComments],
  );

  const loadGitReviewDiff = useCallback(async (path: string, scope: GitReviewScope) => {
    taskActionsRef.current?.leave();
    const client = clientRef.current;
    if (!client || !path) {
      setGitReviewDiff(null);
      return;
    }
    const generation = ++gitReviewRefreshGenerationRef.current;
    const current = () => client === clientRef.current && generation === gitReviewRefreshGenerationRef.current;
    setGitReviewLoading(true);
    setGitReviewError("");
    try {
      const diff = await client.getGitReviewDiff(path, scope);
      if (current()) setGitReviewDiff(diff);
    } catch (error) {
      if (current()) {
        setGitReviewDiff(null);
        setGitReviewError(errorText(error, "读取 Git diff 失败"));
      }
    } finally {
      if (current()) setGitReviewLoading(false);
    }
  }, [clientRef]);

  const refreshGitReview = useCallback(async (
    preferredPath = gitSelectedPath,
    preferredScope = gitReviewScope,
  ) => {
    const client = clientRef.current;
    if (!client) return;
    const generation = ++gitReviewRefreshGenerationRef.current;
    const commentVersion = commentVersionRef.current;
    const current = () => client === clientRef.current && generation === gitReviewRefreshGenerationRef.current;
    setGitReviewLoading(true);
    setGitReviewError("");
    try {
      const [snapshot, comments] = await Promise.all([
        client.getGitReview(),
        client.listGitReviewComments().catch(() => []),
      ]);
      if (!current()) return;
      const selected = snapshot.files.find((file) => file.path === preferredPath) ?? snapshot.files[0];
      if (!selected) {
        setGitReview(snapshot);
        if (commentVersion === commentVersionRef.current) setGitComments(comments);
        setGitSelectedPath("");
        setGitReviewDiff(null);
        return;
      }
      const scope = preferredScope === "staged" && selected.staged
        ? "staged"
        : preferredScope === "working" && selected.unstaged
          ? "working"
          : selected.unstaged ? "working" : "staged";
      const diff = await client.getGitReviewDiff(selected.path, scope);
      if (!current()) return;
      setGitReview(snapshot);
      if (commentVersion === commentVersionRef.current) setGitComments(comments);
      setGitSelectedPath(selected.path);
      setGitReviewScope(scope);
      setGitReviewDiff(diff);
    } catch (error) {
      if (current()) {
        setGitReviewError(errorText(error, "读取 Git 审查状态失败"));
      }
    } finally {
      if (current()) setGitReviewLoading(false);
    }
  }, [clientRef, gitReviewScope, gitSelectedPath]);

  const mutationView: GitMutationView = { snapshot: gitReview, revision: gitReviewRevision?.baseline ?? "",
    commitMessage: gitCommitMessage, prTitle: gitPrTitle, prBase: gitPrBase,
    commentDraft: gitCommentDraft, pendingComment: pendingGitComment, scope: gitReviewScope };
  const mutationLatest = useRef({ clientRef, onBanner, refreshGitReview, refreshPrDelivery, view: mutationView });
  mutationLatest.current = { clientRef, onBanner, refreshGitReview, refreshPrDelivery, view: mutationView };
  const mutationActionsRef = useRef<ReturnType<typeof gitReviewMutations> | null>(null);
  if (!mutationActionsRef.current) mutationActionsRef.current = gitReviewMutations({
    writes: reviewWrites,
    client: () => mutationLatest.current.clientRef.current,
    view: () => mutationLatest.current.view,
    confirm: confirmAction,
    busy: (kind, value) => kind === "action" ? setGitActionBusy(value) : setGitDeliveryBusy(value),
    setSnapshot: snapshot => { mutationLatest.current.view.snapshot = snapshot; setGitReview(snapshot); },
    clearComment: expected => {
      const latest = mutationLatest.current.view;
      if (latest.pendingComment === expected.pendingComment && latest.commentDraft === expected.commentDraft) {
        setPendingGitComment(null);
        setGitCommentDraft(current => current === expected.commentDraft ? "" : current);
      }
    },
    committed: expected => {
      setGitCommitMessage(current => current === expected ? "" : current);
      setGitPrTitle(current => current.trim() ? current : expected.trim());
    },
    refreshGitReview: (path, scope) => mutationLatest.current.refreshGitReview(path, scope),
    refreshPrDelivery: () => mutationLatest.current.refreshPrDelivery(),
    banner: text => mutationLatest.current.onBanner(text),
    openUrl,
  });
  const mutationActions = mutationActionsRef.current;
  const { applyGitAction, commitGitReview, openGitReviewPr } = mutationActions;
  useEffect(() => () => mutationActions.invalidate(), [mutationActions]);

  const taskView: TaskReviewView = { task: taskReviewTask, snapshot: taskBranchReview, diff: taskBranchDiff, path: taskBranchSelectedPath };
  const taskLatest = useRef({ clientRef, view: taskView, onBanner, onShowChanges, onTaskBranchChanged, refreshGitReview });
  taskLatest.current = { clientRef, view: taskView, onBanner, onShowChanges, onTaskBranchChanged, refreshGitReview };
  if (!taskActionsRef.current) taskActionsRef.current = taskBranchReviewActions({
    client: () => taskLatest.current.clientRef.current,
    view: () => taskLatest.current.view,
    writes: reviewWrites,
    capture: () => {
      const client = taskLatest.current.clientRef.current, generation = ++gitReviewRefreshGenerationRef.current;
      return () => client !== null && client === taskLatest.current.clientRef.current && generation === gitReviewRefreshGenerationRef.current;
    },
    setTask: task => { taskLatest.current.view.task = task; setTaskReviewTask(task); },
    setSnapshot: snapshot => { taskLatest.current.view.snapshot = snapshot; setTaskBranchReview(snapshot); },
    setDiff: diff => { taskLatest.current.view.diff = diff; setTaskBranchDiff(diff); },
    setPath: path => { taskLatest.current.view.path = path; setTaskBranchSelectedPath(path); },
    setLoading: setGitReviewLoading, setActionBusy: setGitActionBusy, setVerifying: setTaskReviewVerifying,
    setError: setGitReviewError, confirm: confirmAction,
    show: () => taskLatest.current.onShowChanges(),
    changed: () => taskLatest.current.onTaskBranchChanged(),
    refreshWorkspace: () => taskLatest.current.refreshGitReview(),
    banner: text => taskLatest.current.onBanner(text),
  });
  const taskActions = taskActionsRef.current;
  const { loadTaskBranchReviewDiff, openTaskBranchReview, closeTaskBranchReview, applyTaskBranchAction, verifyReviewedTaskBranch } = taskActions;

  const commentLatest = useRef({ clientRef, onBanner, refreshGitReview, gitReview, gitComments, pendingGitComment, gitCommentDraft });
  commentLatest.current = { clientRef, onBanner, refreshGitReview, gitReview, gitComments, pendingGitComment, gitCommentDraft };
  const commentActionsRef = useRef<ReturnType<typeof gitCommentActions> | null>(null);
  if (!commentActionsRef.current) commentActionsRef.current = gitCommentActions({
    client: () => commentLatest.current.clientRef.current,
    context: () => { const snapshot = commentLatest.current.gitReview; return JSON.stringify([snapshot?.root, snapshot?.branch, snapshot?.head]); },
    pending: () => commentLatest.current.pendingGitComment,
    draft: () => commentLatest.current.gitCommentDraft,
    comments: () => commentLatest.current.gitComments,
    writes: reviewWrites, setComments: setGitComments, setBusy: setGitCommentSaving,
    clearDraft: (pending, draft) => {
      if (commentLatest.current.pendingGitComment === pending && commentLatest.current.gitCommentDraft === draft) {
        setPendingGitComment(null); setGitCommentDraft(current => current === draft ? "" : current);
      }
    },
    banner: text => commentLatest.current.onBanner(text),
    refresh: (path, scope) => commentLatest.current.refreshGitReview(path, scope),
  });
  const commentActions = commentActionsRef.current;
  const { addGitComment, updateGitCommentStatus, deleteGitComment } = commentActions;

  const openGitReviewFile = async (file: GitReviewFile, scope?: GitReviewScope) => {
    const nextScope = scope ?? (file.unstaged ? "working" : "staged");
    setGitSelectedPath(file.path);
    setGitReviewScope(nextScope);
    setPendingGitComment(null);
    setGitCommentDraft("");
    await loadGitReviewDiff(file.path, nextScope);
  };

  const startGitComment = (path: string, hunk: GitReviewHunk, line: number, side: "new" | "old") => {
    setPendingGitComment({
      path,
      scope: gitReviewDiff?.scope ?? gitReviewScope,
      hunkId: hunk.id,
      hunkSha256: hunk.sha256,
      line,
      side,
    });
    setGitCommentDraft("");
  };

  /** 切换项目时同时使工作区、任务审查和评论的旧请求失效。 */
  const resetChangeReview = () => {
    mutationActions.reset();
    taskActions.reset();
    commentActions.reset();
    gitReviewRefreshGenerationRef.current += 1;
    setGitReview(null);
    setGitReviewDiff(null);
    setGitSelectedPath("");
    setGitReviewError("");
    setGitReviewRevision(null);
    setGitComments([]);
    setPendingGitComment(null);
    setGitCommentDraft("");
    prActions.reset();
  };

  return {
    gitReview, gitReviewDiff, gitSelectedPath, gitReviewScope, gitReviewLoading, gitReviewError,
    gitReviewRevision, setGitReviewRevision, gitActionBusy, taskReviewTask,
    taskBranchReview, taskBranchDiff,
    taskBranchSelectedPath, taskReviewVerifying, gitComments,
    setGitComments, pendingGitComment, setPendingGitComment, gitCommentDraft, setGitCommentDraft,
    gitCommentSaving, gitCommitMessage, setGitCommitMessage, gitPrTitle, setGitPrTitle, gitPrBase,
    setGitPrBase, gitDeliveryBusy, prDelivery, prDeliveryLoading, prCheckLogs, openGitComments,
    sentGitComments, refreshGitReview, refreshPrDelivery, loadTaskBranchReviewDiff,
    openTaskBranchReview, closeTaskBranchReview, applyTaskBranchAction, verifyReviewedTaskBranch,
    openGitReviewFile, applyGitAction, startGitComment, addGitComment, updateGitCommentStatus,
    deleteGitComment, loadPrCheckLog, commitGitReview, openGitReviewPr, resetChangeReview,
  };
}
