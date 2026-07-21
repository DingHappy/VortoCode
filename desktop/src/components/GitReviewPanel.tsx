// App.tsx 抽出的「变更」inspector 面板（B8-④c S8，只搬家不改行为）：
// 本地 Git 审查（文件/hunk/行级评论）+ 任务分支审查（F 域视图复用同一面板）+
// PR/CI 交付卡 + 提交与 Draft PR 表单 + Agent 提案 diff。
//
// ④c 实测钉死的边界：E 域是**总线唯一直写的域**（git_review_changed →
// setGitReviewRevision），且 tab 按钮会跨域清 F 域 4 个 state、prDelivery 与决策中心
// 共享、diffPayload 由总线 agent_diff 写入——hook 模式前提不成立，本面板走纯展示件
// 契约（同 S3–S7）：E/F/prDelivery 全部 state 留 App 经 props 下传，写路径（触碰
// client/审计/banner/window.confirm）经回调注入。评论/提交/PR 表单草稿切 tab 不丢，
// 一律留 App。搬进来的只有五个仅本面板消费的派生（selectedGitFile 与 displayedGit* 族）。
import { openUrl } from "@tauri-apps/plugin-opener";

import { DiffViewer } from "./DiffViewer";
import type {
  ConnectionState,
  DiffPayload,
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

type GitReviewPanelProps = {
  connection: ConnectionState;
  busy: boolean;
  diffPayload: DiffPayload | null;
  gitReview: GitReviewSnapshot | null;
  gitReviewDiff: GitReviewDiff | null;
  gitReviewScope: GitReviewScope;
  gitReviewLoading: boolean;
  gitReviewError: string;
  gitReviewRevision: { baseline: string; files: number; reason: string; receivedAt: number } | null;
  gitSelectedPath: string;
  gitActionBusy: boolean;
  gitComments: GitReviewComment[];
  openGitComments: GitReviewComment[];
  sentGitComments: GitReviewComment[];
  pendingGitComment: PendingGitComment | null;
  gitCommentDraft: string;
  gitCommentSaving: boolean;
  gitCommitMessage: string;
  gitPrTitle: string;
  gitPrBase: string;
  gitDeliveryBusy: boolean;
  prDelivery: PrDeliverySnapshot | null;
  prDeliveryLoading: boolean;
  prCheckLogs: Record<string, PrDeliveryCheckLog>;
  taskReviewTask: TaskItem | null;
  taskBranchReview: TaskBranchReviewSnapshot | null;
  taskBranchDiff: TaskBranchReviewDiff | null;
  taskBranchSelectedPath: string;
  taskReviewVerifying: boolean;
  onCloseTaskBranchReview: () => void;
  onOpenTaskBranchReview: (task: TaskItem, preferredPath?: string) => void;
  onRefreshGitReview: () => void;
  onLoadTaskBranchDiff: (task: TaskItem, path: string) => void;
  onOpenGitReviewFile: (file: GitReviewFile, scope?: GitReviewScope) => void;
  onApplyGitAction: (action: GitReviewAction, path: string, hunk?: GitReviewHunk) => void;
  onApplyTaskBranchAction: (action: "accept" | "reject", hunk: GitReviewHunk) => void;
  onVerifyReviewedBranch: () => void;
  onStartGitComment: (path: string, hunk: GitReviewHunk, line: number, side: "new" | "old") => void;
  onCommentDraftChange: (value: string) => void;
  onCancelComment: () => void;
  onAddGitComment: () => void;
  onSendGitComments: () => void;
  onUpdateCommentStatus: (comment: GitReviewComment, status: GitReviewComment["status"]) => void;
  onDeleteGitComment: (comment: GitReviewComment) => void;
  onRefreshPrDelivery: () => void;
  onSendPrFeedback: (check?: PrDeliveryCheck) => void;
  onLoadPrCheckLog: (check: PrDeliveryCheck) => void;
  onCommitMessageChange: (value: string) => void;
  onCommitGitReview: () => void;
  onPrTitleChange: (value: string) => void;
  onPrBaseChange: (value: string) => void;
  onOpenGitReviewPr: () => void;
};

export function GitReviewPanel({
  connection,
  busy,
  diffPayload,
  gitReview,
  gitReviewDiff,
  gitReviewScope,
  gitReviewLoading,
  gitReviewError,
  gitReviewRevision,
  gitSelectedPath,
  gitActionBusy,
  gitComments,
  openGitComments,
  sentGitComments,
  pendingGitComment,
  gitCommentDraft,
  gitCommentSaving,
  gitCommitMessage,
  gitPrTitle,
  gitPrBase,
  gitDeliveryBusy,
  prDelivery,
  prDeliveryLoading,
  prCheckLogs,
  taskReviewTask,
  taskBranchReview,
  taskBranchDiff,
  taskBranchSelectedPath,
  taskReviewVerifying,
  onCloseTaskBranchReview,
  onOpenTaskBranchReview,
  onRefreshGitReview,
  onLoadTaskBranchDiff,
  onOpenGitReviewFile,
  onApplyGitAction,
  onApplyTaskBranchAction,
  onVerifyReviewedBranch,
  onStartGitComment,
  onCommentDraftChange,
  onCancelComment,
  onAddGitComment,
  onSendGitComments,
  onUpdateCommentStatus,
  onDeleteGitComment,
  onRefreshPrDelivery,
  onSendPrFeedback,
  onLoadPrCheckLog,
  onCommitMessageChange,
  onCommitGitReview,
  onPrTitleChange,
  onPrBaseChange,
  onOpenGitReviewPr,
}: GitReviewPanelProps) {
  const selectedGitFile = gitReview?.files.find((file) => file.path === gitSelectedPath) ?? null;
  const displayedGitReview = taskBranchReview ?? gitReview;
  const displayedGitPath = taskBranchReview ? taskBranchSelectedPath : gitSelectedPath;
  const displayedGitFile = displayedGitReview?.files.find((file) => file.path === displayedGitPath) ?? null;
  const displayedGitDiff = taskBranchReview ? taskBranchDiff : gitReviewDiff;

  return (
    <div className="git-review-panel">
      <div className="git-review-head">
        <div>
          <strong>{displayedGitReview?.branch || "Git Review"}</strong>
          <span>{taskBranchReview
            ? `任务 ${taskBranchReview.task_id.slice(0, 10)} · → ${taskBranchReview.base} · HEAD ${taskBranchReview.head}`
            : displayedGitReview?.head ? `HEAD ${displayedGitReview.head}` : "逐文件、逐块审查本地改动"}</span>
          {!taskBranchReview && gitReviewRevision && (
            <span className="git-review-live">实时同步 · {gitReviewRevision.files} 个改动文件 · {new Date(gitReviewRevision.receivedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
          )}
        </div>
        <div>
          {taskBranchReview && <button onClick={() => void onCloseTaskBranchReview()}>返回工作区</button>}
          <button onClick={() => taskReviewTask ? void onOpenTaskBranchReview(taskReviewTask, taskBranchSelectedPath) : void onRefreshGitReview()} disabled={gitReviewLoading}>↻</button>
        </div>
      </div>
      {gitReviewError && <div className="git-review-error">{gitReviewError}</div>}
      {taskBranchReview && (taskBranchReview.review.policy?.error || taskBranchReview.review.policy?.require_all_hunks_decided) && (
        <div className={`task-review-policy ${taskBranchReview.review.policy?.error ? "failed" : taskBranchReview.review.coverage?.complete ? "passed" : "pending"}`}>
          <div>
            <strong>{taskBranchReview.review.policy?.error ? "团队审查策略配置无效" : taskBranchReview.review.coverage?.complete ? "✓ 全部 hunk 已完成决策" : "团队策略 · 全部 hunk 必须完成决策"}</strong>
            <span>{taskBranchReview.review.policy?.error
              || (taskBranchReview.review.coverage?.known
                ? `已接受 ${taskBranchReview.review.coverage.accepted_hunks}/${taskBranchReview.review.coverage.total_hunks} · 待处理 ${taskBranchReview.review.coverage.pending_hunks}`
                : taskBranchReview.review.coverage?.error || "正在确认当前分支覆盖率")}</span>
          </div>
          <code>{taskBranchReview.review.policy?.config_path}</code>
        </div>
      )}
      {taskBranchReview?.review.verification_stale && (
        <div className="task-review-warning">
          <div><strong>审查提交使旧测试证据失效</strong><span>重新验证通过前，Draft PR 交付闸门保持关闭。</span></div>
          <button className="primary" disabled={taskReviewVerifying} onClick={() => void onVerifyReviewedBranch()}>{taskReviewVerifying ? "验证中…" : "在隔离 worktree 重验"}</button>
        </div>
      )}
      {taskBranchReview?.review.verification && (
        <div className={`task-review-verification ${taskBranchReview.review.verification.ok ? "passed" : "failed"}`}>
          <div>
            <strong>{taskBranchReview.review.verification.ok ? "✓ 审查后验证通过" : "验证未通过"}</strong>
            <span>{taskBranchReview.review.verification.cmd} · {taskBranchReview.review.verification.head.slice(0, 12)}</span>
          </div>
          {taskBranchReview.review.verification.output && (
            <details>
              <summary>查看验证证据</summary>
              <pre>{taskBranchReview.review.verification.output}</pre>
            </details>
          )}
        </div>
      )}
      {gitReviewLoading && !displayedGitReview && <div className="file-loading">正在读取 Git 改动…</div>}
      {displayedGitReview && displayedGitReview.files.length === 0 && (
        <div className="panel-empty compact"><strong>{taskBranchReview ? "任务分支没有剩余改动" : "工作区干净"}</strong><p>{taskBranchReview ? "所有 Agent 改动均已撤销，分支仍保留审查提交。" : "Agent 产生修改后会在这里按文件和 hunk 审查。"}</p></div>
      )}
      {displayedGitReview && displayedGitReview.files.length > 0 && (
        <>
          <div className="git-file-list">
            {displayedGitReview.files.map((file) => (
              <button
                className={displayedGitPath === file.path ? "active" : ""}
                key={file.path}
                title={file.path}
                onClick={() => taskReviewTask ? void onLoadTaskBranchDiff(taskReviewTask, file.path) : void onOpenGitReviewFile(file)}
              >
                <b>{file.status}</b>
                <span>{file.path}</span>
                <i>{taskBranchReview ? "任务分支" : file.conflicted ? "冲突" : file.untracked ? "未跟踪" : file.staged && file.unstaged ? "双区" : file.staged ? "已暂存" : "工作区"}</i>
              </button>
            ))}
          </div>
          {displayedGitReview.truncated && <div className="file-list-limit">改动文件超过 2,000 项，列表已截断。</div>}
        </>
      )}

      {displayedGitFile && (
        <>
          <div className="git-file-toolbar">
            {taskBranchReview ? (
              <div className="task-review-scope">
                <span>{taskBranchReview.review.policy?.require_all_hunks_decided && taskBranchReview.review.coverage?.known
                  ? `任务分支改动 · 已接受 ${taskBranchReview.review.coverage.accepted_hunks}/${taskBranchReview.review.coverage.total_hunks}`
                  : `任务分支改动 · ${taskBranchReview.review.accepted_hunks} 个 hunk 已接受`}</span>
                {!taskBranchReview.mutable && <b>{taskBranchReview.mutation_reason}</b>}
              </div>
            ) : (
              <>
                <div className="git-scope-switch">
                  <button
                    className={gitReviewScope === "working" ? "active" : ""}
                    disabled={!selectedGitFile?.unstaged}
                    onClick={() => selectedGitFile && void onOpenGitReviewFile(selectedGitFile, "working")}
                  >工作区</button>
                  <button
                    className={gitReviewScope === "staged" ? "active" : ""}
                    disabled={!selectedGitFile?.staged}
                    onClick={() => selectedGitFile && void onOpenGitReviewFile(selectedGitFile, "staged")}
                  >已暂存</button>
                </div>
                <div className="git-file-actions">
                  {gitReviewScope === "working" ? (
                    <>
                      {!selectedGitFile?.untracked && <button className="danger" disabled={gitActionBusy} onClick={() => selectedGitFile && void onApplyGitAction("revert", selectedGitFile.path)}>撤销文件</button>}
                      <button className="primary" disabled={gitActionBusy} onClick={() => selectedGitFile && void onApplyGitAction("stage", selectedGitFile.path)}>暂存文件</button>
                    </>
                  ) : (
                    <button disabled={gitActionBusy} onClick={() => selectedGitFile && void onApplyGitAction("unstage", selectedGitFile.path)}>取消暂存</button>
                  )}
                </div>
              </>
            )}
          </div>
          <div className="git-selected-path" title={displayedGitFile.path}>{displayedGitFile.path}</div>
        </>
      )}

      {gitReviewLoading && displayedGitReview && <div className="file-loading">正在刷新 diff…</div>}
      {!gitReviewLoading && displayedGitDiff?.binary && (
        <div className="panel-empty compact"><strong>二进制改动</strong><p>{taskBranchReview ? "任务分支二进制文件暂时只读，请使用外部 Git 工具审查。" : "可整文件暂存或取消暂存，不能进行行级审查。"}</p></div>
      )}
      {!gitReviewLoading && displayedGitDiff && !displayedGitDiff.binary && displayedGitDiff.hunks.length === 0 && displayedGitFile && (
        <div className="panel-empty compact"><strong>这个区域没有 diff</strong><p>{taskBranchReview ? "刷新任务分支查看最新审查结果。" : "切换“工作区/已暂存”查看另一侧改动。"}</p></div>
      )}
      {!gitReviewLoading && displayedGitDiff?.hunks.map((hunk) => (
        <section className={`git-hunk ${hunk.accepted ? "accepted" : ""}`} key={`${displayedGitDiff.scope}-${hunk.id}-${hunk.sha256}`}>
          <div className="git-hunk-head">
            <div>
              <b title={`稳定变更标识 ${hunk.id}`}>{hunk.id.slice(0, 10)}</b>
              <em className={`git-source ${hunk.source}`} title={hunk.source_at ? `${hunk.source_tool || "edit"} · ${hunk.source_at}` : "未发现 VortoCode 记录"}>
                {hunk.source === "agent" ? "Agent" : hunk.source === "user" ? "你修改" : hunk.source === "hook" ? "Hook" : hunk.source === "mixed" ? "混合修改" : "外部修改"}
              </em>
              <span>{hunk.header}</span>
            </div>
            <div>
              {taskBranchReview ? (
                <>
                  {!taskBranchReview.mutable ? (
                    <span className="hunk-readonly">只读</span>
                  ) : (
                    <>
                      {hunk.accepted ? <span className="hunk-accepted">✓ 已接受</span> : <button className="primary" disabled={gitActionBusy} onClick={() => void onApplyTaskBranchAction("accept", hunk)}>接受</button>}
                      <button className="danger" disabled={gitActionBusy} onClick={() => void onApplyTaskBranchAction("reject", hunk)}>撤销</button>
                    </>
                  )}
                </>
              ) : displayedGitDiff.scope === "working" ? (
                <>
                  {!selectedGitFile?.untracked && <button className="danger" disabled={gitActionBusy} onClick={() => void onApplyGitAction("revert", displayedGitDiff.path, hunk)}>撤销</button>}
                  <button className="primary" disabled={gitActionBusy} onClick={() => void onApplyGitAction("stage", displayedGitDiff.path, hunk)}>暂存</button>
                </>
              ) : (
                <button disabled={gitActionBusy} onClick={() => void onApplyGitAction("unstage", displayedGitDiff.path, hunk)}>取消暂存</button>
              )}
            </div>
          </div>
          <div className="git-hunk-lines">
            {hunk.lines.map((line, index) => {
              const lineNumber = line.new_line ?? line.old_line;
              const side: "new" | "old" = line.new_line != null ? "new" : "old";
              const commentable = lineNumber != null && line.kind !== "header" && line.kind !== "meta";
              const selected = Boolean(
                !taskBranchReview
                && gitReviewDiff
                && pendingGitComment
                && pendingGitComment.path === gitReviewDiff.path
                && pendingGitComment.hunkId === hunk.id
                && pendingGitComment.line === lineNumber
                && pendingGitComment.side === side,
              );
              return (
                <div className={`git-review-line ${line.kind} ${selected ? "selected" : ""}`} key={`${index}-${line.text.slice(0, 16)}`}>
                  <span>{line.old_line ?? ""}</span>
                  <span>{line.new_line ?? ""}</span>
                  <code>{line.text || " "}</code>
                  {!taskBranchReview && gitReviewDiff && commentable && (
                    <button title="添加行级评论" onClick={() => onStartGitComment(gitReviewDiff.path, hunk, lineNumber!, side)}>＋</button>
                  )}
                </div>
              );
            })}
          </div>
          {!taskBranchReview && gitReviewDiff && pendingGitComment?.path === gitReviewDiff.path && pendingGitComment.hunkId === hunk.id && (
            <div className="git-comment-composer">
              <span>{pendingGitComment.side === "new" ? "+" : "-"}{pendingGitComment.line}</span>
              <textarea value={gitCommentDraft} onChange={(event) => onCommentDraftChange(event.target.value)} placeholder="说明需要怎样修改，以及原因…" autoFocus />
              <div>
                <button onClick={onCancelComment}>取消</button>
                <button className="primary" disabled={!gitCommentDraft.trim() || gitCommentSaving} onClick={() => void onAddGitComment()}>{gitCommentSaving ? "保存中…" : "加入审查"}</button>
              </div>
            </div>
          )}
        </section>
      ))}

      {!taskBranchReview && gitComments.length > 0 && (
        <div className="git-review-comments">
          <div className="git-review-comments-head">
            <strong>{openGitComments.length} 条待发送{sentGitComments.length > 0 ? ` · ${sentGitComments.length} 条待确认` : ""}</strong>
            <button className="primary" disabled={busy || connection !== "connected" || openGitComments.length === 0} onClick={() => void onSendGitComments()}>交给 Agent 修复</button>
          </div>
          {gitComments.map((comment) => (
            <div className={`git-review-comment ${comment.status}`} key={comment.id}>
              <div className="git-review-comment-meta">
                <span>{comment.path}:{comment.line} · {comment.scope === "staged" ? "已暂存" : "工作区"} · {comment.hunkId.slice(0, 10)}</span>
                <em>{comment.status === "open" ? "待发送" : comment.status === "sent" ? "待确认" : "已解决"}</em>
              </div>
              <p>{comment.body}</p>
              <div className="git-review-comment-actions">
                {comment.status === "sent" && <button disabled={gitCommentSaving} onClick={() => void onUpdateCommentStatus(comment, "resolved")}>标记解决</button>}
                {comment.status !== "open" && <button disabled={gitCommentSaving} onClick={() => void onUpdateCommentStatus(comment, "open")}>重新打开</button>}
                <button title="删除评论" disabled={gitCommentSaving} onClick={() => void onDeleteGitComment(comment)}>删除</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {!taskBranchReview && prDelivery && (
        <div className={`pr-delivery-card ${prDelivery.ok ? "ready" : "unavailable"}`}>
          <div className="pr-delivery-head">
            <div>
              <strong>{prDelivery.ok ? `PR #${prDelivery.pr} · ${prDelivery.title || prDelivery.branch}` : "PR / CI"}</strong>
              {prDelivery.ok && <span>{prDelivery.draft ? "Draft" : prDelivery.state || "OPEN"} · → {prDelivery.base || "base"}</span>}
            </div>
            <div>
              {prDelivery.url && <button onClick={() => void openUrl(prDelivery.url!)}>GitHub ↗</button>}
              <button onClick={() => void onRefreshPrDelivery()} disabled={prDeliveryLoading}>{prDeliveryLoading ? "读取中…" : "刷新"}</button>
            </div>
          </div>
          {!prDelivery.ok ? (
            <p className="pr-delivery-empty">{prDelivery.error || "当前分支还没有关联 PR"}</p>
          ) : (
            <>
              <div className="pr-delivery-summary">
                <span className={prDelivery.summary.failed ? "failed" : "passed"}>{prDelivery.summary.failed} 失败</span>
                <span className={prDelivery.summary.pending ? "pending" : "muted"}>{prDelivery.summary.pending} 运行中</span>
                <span className="passed">{prDelivery.summary.passed} 通过</span>
                {prDelivery.review_decision && <span className={prDelivery.review_decision === "APPROVED" ? "passed" : "pending"}>Review · {prDelivery.review_decision}</span>}
                {prDelivery.merge_state && <span className="muted">Merge · {prDelivery.merge_state}</span>}
              </div>
              {prDelivery.comments.length > 0 && (
                <div className="pr-review-feedback">
                  <div><strong>{prDelivery.comments.length} 条未解决 Review</strong><button disabled={busy} onClick={() => void onSendPrFeedback()}>交给 Agent</button></div>
                  {prDelivery.comments.slice(0, 8).map((comment, index) => (
                    <p key={`${comment.author}-${comment.path}-${comment.line}-${index}`}>
                      <span>{comment.path ? `${comment.path}${comment.line ? `:${comment.line}` : ""}` : `@${comment.author}`}</span>
                      {comment.body}
                    </p>
                  ))}
                </div>
              )}
              <div className="pr-checks">
                {prDelivery.checks.length === 0 && <p>GitHub 尚未返回 CI checks。</p>}
                {prDelivery.checks.map((check) => {
                  const checkLog = prCheckLogs[check.id];
                  const checkState = check.failing ? "failed" : check.pending ? "pending" : "passed";
                  return (
                    <div className={`pr-check ${checkState}`} key={check.id}>
                      <div className="pr-check-title">
                        <i>{check.failing ? "!" : check.pending ? "…" : "✓"}</i>
                        <div><strong>{check.name}</strong>{check.workflow && <span>{check.workflow}</span>}</div>
                        <span>{check.conclusion || check.state || check.status || "UNKNOWN"}</span>
                      </div>
                      {check.failing && (
                        <div className="pr-check-actions">
                          {check.link && <button onClick={() => void openUrl(check.link!)}>详情 ↗</button>}
                          <button onClick={() => void onLoadPrCheckLog(check)}>失败日志</button>
                          <button className="primary" disabled={busy} onClick={() => void onSendPrFeedback(check)}>交给 Agent 修复</button>
                        </div>
                      )}
                      {checkLog && (
                        <div className="pr-check-log">
                          {checkLog.log?.job_name && <strong>{checkLog.log.job_name}{checkLog.log.step_name ? ` › ${checkLog.log.step_name}` : ""}</strong>}
                          {checkLog.log?.excerpt ? <pre>{checkLog.log.excerpt}</pre> : <p>{checkLog.log?.error || checkLog.error || "没有可用失败日志"}</p>}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      )}

      {!taskBranchReview && gitReview && (
        <div className="git-delivery-card">
          <div className="git-delivery-heading">
            <strong>提交与交付</strong>
            <span>{gitReview.files.filter((file) => file.staged).length} 个文件含已暂存改动</span>
          </div>
          <div className="git-commit-form">
            <input
              value={gitCommitMessage}
              onChange={(event) => onCommitMessageChange(event.target.value)}
              placeholder="提交说明（只提交已暂存范围）"
              onKeyDown={(event) => {
                if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) void onCommitGitReview();
              }}
            />
            <button
              className="primary"
              disabled={gitReview.files.every((file) => !file.staged) || !gitCommitMessage.trim() || gitDeliveryBusy}
              onClick={() => void onCommitGitReview()}
            >提交</button>
          </div>
          <div className="git-pr-form">
            <input value={gitPrTitle} onChange={(event) => onPrTitleChange(event.target.value)} placeholder="Draft PR 标题" />
            <input className="git-base-input" value={gitPrBase} onChange={(event) => onPrBaseChange(event.target.value)} aria-label="PR base 分支" />
            <button
              disabled={!gitPrTitle.trim() || !gitPrBase.trim() || gitDeliveryBusy || ["main", "master", "develop", "development"].includes(gitReview.branch.toLowerCase())}
              onClick={() => void onOpenGitReviewPr()}
            >开 Draft PR</button>
          </div>
          {["main", "master", "develop", "development"].includes(gitReview.branch.toLowerCase()) && (
            <p>受保护分支不能直接创建 PR；先让任务落到功能分支或 Worktree。</p>
          )}
        </div>
      )}

      {diffPayload?.diff && (
        <details className="agent-diff-proposal">
          <summary>Agent 最近一次提案 Diff</summary>
          <DiffViewer payload={diffPayload} />
        </details>
      )}
    </div>
  );
}
