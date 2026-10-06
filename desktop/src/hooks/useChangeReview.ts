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
// 红线 3：loadGitReviewDiff / refreshGitReview / refreshPrDelivery 被 App 的 useCallback 依赖，
// 依赖数组与迁移前一致（只多了恒等的 clientRef）。其余处理函数迁移前就是普通函数，保持原样。
import { openUrl } from "@tauri-apps/plugin-opener";
import { useCallback, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";

import type { GatewayClient } from "../gateway";
import { isProtectedBranch } from "../lib/branches";
import { errorText } from "../lib/errorText";
import { confirmAction } from "../lib/confirm";
import type {
  GitReviewAction,
  GitReviewComment,
  GitReviewDiff,
  GitReviewFile,
  GitReviewHunk,
  GitReviewScope,
  GitReviewSnapshot,
  PendingGitComment,
  PrDeliveryCheck,
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
  const [gitComments, setGitComments] = useState<GitReviewComment[]>([]);
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
    const client = clientRef.current;
    if (!client || !path) {
      setGitReviewDiff(null);
      return;
    }
    const generation = ++gitReviewRefreshGenerationRef.current;
    setGitReviewLoading(true);
    setGitReviewError("");
    setTaskReviewTask(null);
    setTaskBranchReview(null);
    setTaskBranchDiff(null);
    setTaskBranchSelectedPath("");
    setTaskReviewVerifying(false);
    try {
      const diff = await client.getGitReviewDiff(path, scope);
      if (generation === gitReviewRefreshGenerationRef.current) setGitReviewDiff(diff);
    } catch (error) {
      if (generation === gitReviewRefreshGenerationRef.current) {
        setGitReviewDiff(null);
        setGitReviewError(errorText(error, "读取 Git diff 失败"));
      }
    } finally {
      if (generation === gitReviewRefreshGenerationRef.current) setGitReviewLoading(false);
    }
  }, [clientRef]);

  const refreshGitReview = useCallback(async (
    preferredPath = gitSelectedPath,
    preferredScope = gitReviewScope,
  ) => {
    const client = clientRef.current;
    if (!client) return;
    const generation = ++gitReviewRefreshGenerationRef.current;
    setGitReviewLoading(true);
    setGitReviewError("");
    try {
      const [snapshot, comments] = await Promise.all([
        client.getGitReview(),
        client.listGitReviewComments().catch(() => []),
      ]);
      if (generation !== gitReviewRefreshGenerationRef.current) return;
      const selected = snapshot.files.find((file) => file.path === preferredPath) ?? snapshot.files[0];
      if (!selected) {
        setGitReview(snapshot);
        setGitComments(comments);
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
      if (generation !== gitReviewRefreshGenerationRef.current) return;
      setGitReview(snapshot);
      setGitComments(comments);
      setGitSelectedPath(selected.path);
      setGitReviewScope(scope);
      setGitReviewDiff(diff);
    } catch (error) {
      if (generation === gitReviewRefreshGenerationRef.current) {
        setGitReviewError(errorText(error, "读取 Git 审查状态失败"));
      }
    } finally {
      if (generation === gitReviewRefreshGenerationRef.current) setGitReviewLoading(false);
    }
  }, [clientRef, gitReviewScope, gitSelectedPath]);

  const refreshPrDelivery = useCallback(async () => {
    const client = clientRef.current;
    if (!client) return;
    setPrDeliveryLoading(true);
    try {
      const snapshot = await client.getPrDelivery();
      setPrDelivery(snapshot);
      setPrCheckLogs((previous) => Object.fromEntries(
        Object.entries(previous).filter(([checkId]) => snapshot.failing_checks.some((check) => check.id === checkId)),
      ));
    } catch (error) {
      setPrDelivery({
        ok: false,
        branch: "",
        comments: [],
        checks: [],
        failing_checks: [],
        summary: { total: 0, failed: 0, pending: 0, passed: 0 },
        error: errorText(error, "读取 PR/CI 状态失败"),
      });
    } finally {
      setPrDeliveryLoading(false);
    }
  }, [clientRef]);

  const loadTaskBranchReviewDiff = async (task: TaskItem, path: string) => {
    const client = clientRef.current;
    if (!client || !path) {
      setTaskBranchDiff(null);
      return;
    }
    setGitReviewLoading(true);
    setGitReviewError("");
    try {
      setTaskBranchSelectedPath(path);
      setTaskBranchDiff(await client.getTaskBranchReviewDiff(task.id, path));
    } catch (error) {
      setTaskBranchDiff(null);
      setGitReviewError(errorText(error, "读取任务分支 diff 失败"));
    } finally {
      setGitReviewLoading(false);
    }
  };

  const openTaskBranchReview = async (task: TaskItem, preferredPath = "") => {
    const client = clientRef.current;
    if (!client || (!task.branch && !task.plan?.branch)) {
      onBanner("这个任务还没有可审查的分支");
      return;
    }
    setTaskReviewTask(task);
    setTaskBranchReview(null);
    setTaskBranchDiff(null);
    setGitReviewError("");
    onShowChanges();
    setGitReviewLoading(true);
    try {
      const snapshot = await client.getTaskBranchReview(task.id);
      setTaskBranchReview(snapshot);
      const selected = snapshot.files.find((file) => file.path === preferredPath) ?? snapshot.files[0];
      if (!selected) {
        setTaskBranchSelectedPath("");
        return;
      }
      setTaskBranchSelectedPath(selected.path);
      setTaskBranchDiff(await client.getTaskBranchReviewDiff(task.id, selected.path));
    } catch (error) {
      setGitReviewError(errorText(error, "读取任务分支审查失败"));
    } finally {
      setGitReviewLoading(false);
    }
  };

  const closeTaskBranchReview = async () => {
    setTaskReviewTask(null);
    setTaskBranchReview(null);
    setTaskBranchDiff(null);
    setTaskBranchSelectedPath("");
    await refreshGitReview();
  };

  const applyTaskBranchAction = async (
    action: "accept" | "reject",
    hunk: GitReviewHunk,
  ) => {
    const client = clientRef.current;
    if (!client || !taskReviewTask || !taskBranchReview || !taskBranchDiff) return;
    if (action === "reject" && !await confirmAction(
      `确定撤销任务分支 ${taskBranchReview.branch} 中 ${taskBranchDiff.path} 的这个改动块吗？\n\n将创建一条审查提交，旧测试证据会立即失效。`,
    )) return;
    setGitActionBusy(true);
    setGitReviewError("");
    try {
      const result = await client.applyTaskBranchReviewAction(taskReviewTask.id, {
        action,
        path: taskBranchDiff.path,
        hunk_id: hunk.id,
        expected_sha256: hunk.sha256,
        confirm: action === "reject",
      });
      setTaskBranchReview(result.snapshot);
      const selected = result.snapshot.files.find((file) => file.path === taskBranchDiff.path)
        ?? result.snapshot.files[0];
      if (selected) {
        setTaskBranchSelectedPath(selected.path);
        setTaskBranchDiff(await client.getTaskBranchReviewDiff(taskReviewTask.id, selected.path));
      } else {
        setTaskBranchSelectedPath("");
        setTaskBranchDiff(null);
      }
      await onTaskBranchChanged();
      onBanner(action === "accept"
        ? "已记录稳定 hunk 接受证据"
        : "已在任务分支创建撤销提交；重新验证通过前不能开 PR");
    } catch (error) {
      setGitReviewError(errorText(error, "任务分支审查操作失败"));
    } finally {
      setGitActionBusy(false);
    }
  };

  const verifyReviewedTaskBranch = async (targetTask: TaskItem | null = taskReviewTask) => {
    const client = clientRef.current;
    if (!client || !targetTask) return;
    setTaskReviewTask(targetTask);
    setTaskReviewVerifying(true);
    setGitReviewError("");
    try {
      const result = await client.verifyTaskBranchReview(targetTask.id);
      setTaskBranchReview(result.snapshot);
      await onTaskBranchChanged();
      onBanner(result.ok ? "任务分支已在隔离 worktree 重新验证通过，可以继续交付" : "重新验证未通过，PR 闸门保持关闭");
    } catch (error) {
      const message = errorText(error, "任务分支重新验证失败");
      setGitReviewError(message);
      onBanner(message);
    } finally {
      setTaskReviewVerifying(false);
    }
  };

  const openGitReviewFile = async (file: GitReviewFile, scope?: GitReviewScope) => {
    const nextScope = scope ?? (file.unstaged ? "working" : "staged");
    setGitSelectedPath(file.path);
    setGitReviewScope(nextScope);
    setPendingGitComment(null);
    setGitCommentDraft("");
    await loadGitReviewDiff(file.path, nextScope);
  };

  const applyGitAction = async (
    action: GitReviewAction,
    path: string,
    hunk?: GitReviewHunk,
  ) => {
    if (!clientRef.current || gitActionBusy) return;
    const destructive = action === "revert";
    if (destructive) {
      const target = hunk ? `${path} 的 ${hunk.id}` : path;
      if (!await confirmAction(`撤销 ${target} 的本地修改？这会丢弃对应内容，且不可从 VortoCode 恢复。`)) return;
    }
    setGitActionBusy(true);
    try {
      const result = await clientRef.current.applyGitReviewAction({
        action,
        path,
        scope: action === "unstage" ? "staged" : "working",
        hunk_id: hunk?.id,
        expected_sha256: hunk?.sha256,
        confirm: destructive,
      });
      setGitReview(result.snapshot);
      setPendingGitComment(null);
      setGitCommentDraft("");
      const preferredScope: GitReviewScope = action === "unstage" ? "staged" : "working";
      await refreshGitReview(path, preferredScope);
      onBanner(action === "stage" ? "Git 改动已暂存" : action === "unstage" ? "Git 改动已取消暂存" : "本地改动已撤销");
    } catch (error) {
      onBanner(errorText(error, "Git 操作失败"));
      await refreshGitReview(path, gitReviewScope);
    } finally {
      setGitActionBusy(false);
    }
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

  const addGitComment = async () => {
    const body = gitCommentDraft.trim();
    const client = clientRef.current;
    if (!client || !pendingGitComment || !body || gitCommentSaving) return;
    setGitCommentSaving(true);
    try {
      const created = await client.createGitReviewComment({
        path: pendingGitComment.path,
        scope: pendingGitComment.scope,
        hunk_id: pendingGitComment.hunkId,
        expected_sha256: pendingGitComment.hunkSha256,
        line: pendingGitComment.line,
        side: pendingGitComment.side,
        body,
      });
      setGitComments((previous) => [created, ...previous]);
      setPendingGitComment(null);
      setGitCommentDraft("");
    } catch (error) {
      onBanner(errorText(error, "保存行级评论失败"));
      await refreshGitReview(pendingGitComment.path, pendingGitComment.scope);
    } finally {
      setGitCommentSaving(false);
    }
  };

  const updateGitCommentStatus = async (
    comment: GitReviewComment,
    status: GitReviewComment["status"],
  ) => {
    const client = clientRef.current;
    if (!client || gitCommentSaving) return;
    setGitCommentSaving(true);
    try {
      const updated = await client.updateGitReviewComment(comment.id, status);
      setGitComments((previous) => previous.map((item) => item.id === updated.id ? updated : item));
    } catch (error) {
      onBanner(errorText(error, "更新审查评论失败"));
    } finally {
      setGitCommentSaving(false);
    }
  };

  const deleteGitComment = async (comment: GitReviewComment) => {
    const client = clientRef.current;
    if (!client || gitCommentSaving) return;
    setGitCommentSaving(true);
    try {
      await client.deleteGitReviewComment(comment.id);
      setGitComments((previous) => previous.filter((item) => item.id !== comment.id));
    } catch (error) {
      onBanner(errorText(error, "删除审查评论失败"));
    } finally {
      setGitCommentSaving(false);
    }
  };

  const loadPrCheckLog = async (check: PrDeliveryCheck): Promise<PrDeliveryCheckLog | null> => {
    if (!clientRef.current) return null;
    try {
      const result = await clientRef.current.getPrCheckLog(check.id);
      setPrCheckLogs((previous) => ({ ...previous, [check.id]: result }));
      return result;
    } catch (error) {
      onBanner(errorText(error, "读取 CI 失败日志失败"));
      return null;
    }
  };

  const commitGitReview = async () => {
    const message = gitCommitMessage.trim();
    if (!clientRef.current || !message || gitDeliveryBusy) return;
    // 受保护分支上多问一次：提交到 main 之后就不能从 main 开 PR 了（后端也拦，这里只是
    // 早一步把原因说清楚，而不是等请求失败再弹一条报错）。
    const onProtected = isProtectedBranch(gitReview?.branch);
    if (onProtected && !await confirmAction(
      `当前在受保护分支 ${gitReview?.branch} 上。\n\n直接提交到这里之后就不能从它开 PR 了`
      + `（需要先切到功能分支）。确认要直接提交到 ${gitReview?.branch} 吗？`)) return;
    setGitDeliveryBusy(true);
    try {
      const result = await clientRef.current.commitGitReview(message, onProtected);
      setGitReview(result.snapshot);
      setGitCommitMessage("");
      if (!gitPrTitle.trim()) setGitPrTitle(message);
      await refreshGitReview();
      onBanner(`已提交审查范围 · ${result.sha}`);
    } catch (error) {
      onBanner(errorText(error, "Git 提交失败"));
    } finally {
      setGitDeliveryBusy(false);
    }
  };

  const openGitReviewPr = async () => {
    const title = gitPrTitle.trim();
    const base = gitPrBase.trim();
    if (!clientRef.current || !title || !base || gitDeliveryBusy) return;
    if (!await confirmAction(`把当前分支 ${gitReview?.branch || "(unknown)"} push 到 origin，并向 ${base} 创建 Draft PR？`)) return;
    setGitDeliveryBusy(true);
    try {
      const result = await clientRef.current.openGitReviewPr({
        title,
        body: `由 VortoCode Desktop 在逐文件、逐 hunk 审查后创建。`,
        base,
        confirm: true,
      });
      onBanner(`Draft PR 已创建：${result.url}`);
      await refreshPrDelivery();
      if (result.url) await openUrl(result.url);
    } catch (error) {
      onBanner(errorText(error, "创建 Draft PR 失败"));
    } finally {
      setGitDeliveryBusy(false);
    }
  };

  /** 切换项目时清空（与迁移前 clearProjectView 内的块一致：任务分支审查/范围/loading 不在此列）。 */
  const resetChangeReview = () => {
    gitReviewRefreshGenerationRef.current += 1;
    setGitReview(null);
    setGitReviewDiff(null);
    setGitSelectedPath("");
    setGitReviewError("");
    setGitReviewRevision(null);
    setGitComments([]);
    setPendingGitComment(null);
    setGitCommentDraft("");
    setPrDelivery(null);
    setPrCheckLogs({});
  };

  return {
    gitReview, gitReviewDiff, gitSelectedPath, gitReviewScope, gitReviewLoading, gitReviewError,
    gitReviewRevision, setGitReviewRevision, gitActionBusy, taskReviewTask, setTaskReviewTask,
    taskBranchReview, setTaskBranchReview, taskBranchDiff, setTaskBranchDiff,
    taskBranchSelectedPath, setTaskBranchSelectedPath, taskReviewVerifying, gitComments,
    setGitComments, pendingGitComment, setPendingGitComment, gitCommentDraft, setGitCommentDraft,
    gitCommentSaving, gitCommitMessage, setGitCommitMessage, gitPrTitle, setGitPrTitle, gitPrBase,
    setGitPrBase, gitDeliveryBusy, prDelivery, prDeliveryLoading, prCheckLogs, openGitComments,
    sentGitComments, refreshGitReview, refreshPrDelivery, loadTaskBranchReviewDiff,
    openTaskBranchReview, closeTaskBranchReview, applyTaskBranchAction, verifyReviewedTaskBranch,
    openGitReviewFile, applyGitAction, startGitComment, addGitComment, updateGitCommentStatus,
    deleteGitComment, loadPrCheckLog, commitGitReview, openGitReviewPr, resetChangeReview,
  };
}
