import type { TaskItem, DevPlanGraph } from "../types";
import { statusLabel, taskReviewGateReason } from "../lib/labels";
import { taskCollaborationDraft, type CollaborationAction } from "../lib/taskCollaboration";
import { DevPlanDagLoader } from "./DevPlanDag";
import { DispatchTaskActions } from "./DispatchTaskActions";
import { TaskHandoffActions } from "./TaskHandoffActions";
import { TaskQuestionActions } from "./TaskQuestionActions";
import { TaskDependencyActions } from "./TaskDependencyActions";
import { TaskChainBudget } from "./TaskChainBudget";
import { TaskCoverageActions } from "./TaskCoverageActions";
import type { GatewayClient } from "../gateway";

type Props = {
  task: TaskItem;
  focused: boolean;
  taskReviewVerifying: boolean;
  loadPlanGraph: (id: string) => Promise<DevPlanGraph>;
  onPause: (task: TaskItem) => unknown;
  onCancel: (task: TaskItem) => unknown;
  onResume: (task: TaskItem) => unknown;
  onCopy: (task: TaskItem) => unknown;
  onReviewBranch: (task: TaskItem) => unknown;
  onVerify: (task: TaskItem) => unknown;
  onOpenPr: (task: TaskItem) => unknown;
  onCollaboration: (task: TaskItem, action: CollaborationAction) => unknown;
  getClient: () => GatewayClient | null;
  onDispatchUpdated: () => void;
};

export function TaskCard({ task, focused, taskReviewVerifying, loadPlanGraph,
  onPause, onCancel, onResume, onCopy, onReviewBranch, onVerify, onOpenPr, onCollaboration,
  getClient, onDispatchUpdated }: Props) {
  return (
    <section
      className={`task-card ${focused ? "focused" : ""}`}
      id={`task-card-${task.id}`}
    >
      <div className="task-card-head">
        <span className={`task-status ${task.status}`}>{task.status === "blocked" ? "等待回答" : statusLabel(task.status)}</span>
        <small>{task.id.slice(0, 8)}</small>
      </div>
      <p>{task.prompt || "后台开发任务"}</p>
      {task.kind !== "delegation" && <div className="task-linkage" title={`${task.id} → ${task.plan_id || "计划生成中"} → ${task.branch || task.plan?.branch || "分支生成中"}`}>
        <span>Task {task.id.slice(0, 8)}</span>
        <b>→</b>
        <span>Plan {task.plan_id ? task.plan_id.slice(0, 14) : "生成中"}</span>
        <b>→</b>
        <span>{task.branch || task.plan?.branch || "分支生成中"}</span>
      </div>}
      {(task.worktrees?.length ?? 0) > 0 && (
        <div className="task-live-worktrees">
          <i />
          <span>{task.worktrees?.length} 个隔离 worktree 正在执行</span>
          <small>{task.worktrees?.map((worktree) => worktree.id).join(" · ")}</small>
        </div>
      )}
      {task.branch_review && (task.branch_review.accepted_hunks > 0 || task.branch_review.verification_stale || task.branch_review.policy?.require_all_hunks_decided || task.branch_review.policy?.error) && (
        <div className={`task-review-state ${taskReviewGateReason(task.branch_review) ? "stale" : "ready"}`}>
          <span>{task.branch_review.policy?.require_all_hunks_decided && task.branch_review.coverage?.known
            ? `已接受 ${task.branch_review.coverage.accepted_hunks}/${task.branch_review.coverage.total_hunks}`
            : `${task.branch_review.accepted_hunks} 个 hunk 已接受`}</span>
          <b>{task.branch_review.verification_stale
            ? "审查后待重验"
            : task.branch_review.policy?.error
              ? "团队策略无效"
              : task.branch_review.policy?.require_all_hunks_decided && !task.branch_review.coverage?.complete
                ? `${task.branch_review.coverage?.pending_hunks ?? "?"} 个待决策`
                : task.branch_review.policy?.require_all_hunks_decided
                  ? "策略已满足"
                  : "审查证据已保存"}</b>
        </div>
      )}
      {task.branch && <code>{task.branch}</code>}
      {task.parent_task_id && <code>接续自 · {task.parent_task_id}</code>}
      {task.plan && (
        <div className="task-plan-progress">
          <div>
            <span>计划 {task.plan.progress.landed}/{task.plan.progress.total}</span>
            <small>{task.plan.status}</small>
          </div>
          <div className="task-plan-bar"><i style={{ width: `${task.plan.progress.total ? (task.plan.progress.landed / task.plan.progress.total) * 100 : 0}%` }} /></div>
          {task.plan.blocks.length > 0 && task.plan_id && (
            <details>
              <summary>依赖图 · {task.plan.blocks.length} 个计划块</summary>
              {/* refreshKey 由进度计数派生：块状态一变（listTasks 周期带回）就重取图。
                  展开才加载——列表里几十张任务卡同时拉图会白打一排请求。 */}
              <DevPlanDagLoader
                load={() => loadPlanGraph(task.plan_id!)}
                refreshKey={`${task.plan_id}:${task.plan.progress.landed}:${task.plan.progress.failed}:${task.plan.progress.running}`}
              />
            </details>
          )}
        </div>
      )}
      {task.error && <div className="task-error">{task.error}</div>}
      {task.kind === "delegation" && task.collaboration && (
        <div className="task-collaboration">
          <p>执行 Agent：{task.collaboration.assignee || "研究员"} · 第 {task.collaboration.round} 轮</p>
          <strong>{task.dependencies?.result_valid === false ? "依赖已失效 · 历史记录保留" : task.collaboration.review === "accepted" ? "已验收" : task.collaboration.review === "pending" ? "执行结束 · 待验收" : task.collaboration.review === "rework_requested" ? "已请求返工" : "尚未提交结果"}</strong>
          {task.result && <details><summary>查看交付结果</summary><pre>{task.result}</pre></details>}
          {task.collaboration.messages.length > 0 && <details><summary>Agent 任务消息</summary>
            {task.collaboration.messages.map((message) => <div key={message.id}><small>{message.sender || "研究员"} · {message.kind} · 第 {message.round} 轮</small><pre>{message.body}</pre></div>)}
          </details>}
          <div className="task-actions">
            {taskCollaborationDraft(task, "review") && <button onClick={() => onCollaboration(task, "review")}>交给主 Agent 验收</button>}
            {taskCollaborationDraft(task, "followup") && <button onClick={() => onCollaboration(task, "followup")}>补充要求 / 返工</button>}
          </div>
          <DispatchTaskActions task={task} getClient={getClient} onUpdated={onDispatchUpdated} />
        </div>
      )}
      <TaskQuestionActions task={task} getClient={getClient} onUpdated={onDispatchUpdated} />
      <TaskDependencyActions task={task} getClient={getClient} onUpdated={onDispatchUpdated} />
      <TaskChainBudget budget={task.chain_budget} />
      <TaskCoverageActions task={task} getClient={getClient} onUpdated={onDispatchUpdated} />
      {task.handoff?.handling && <small title="处理回执与任务验收分别记录">
        {task.handoff.handling.handled ? "交接已处理" : "结果待处理"}
      </small>}
      {task.handoff?.text && (
        <details className="task-handoff">
          <summary>任务交接摘要</summary>
          <pre>{task.handoff.text}</pre>
        </details>
      )}
      <TaskHandoffActions task={task} getClient={getClient} onUpdated={onDispatchUpdated} />
      <div className="task-actions">
        {task.can_pause && <button className="primary" onClick={() => void onPause(task)}>暂停</button>}
        {task.kind !== "delegation" && ["running", "queued", "blocked"].includes(task.status) && <button onClick={() => void onCancel(task)}>取消</button>}
        {task.can_resume && <button className="primary" onClick={() => void onResume(task)}>恢复</button>}
        {task.handoff?.text && <button onClick={() => void onCopy(task)}>复制交接</button>}
        {(task.branch || task.plan?.branch) && <button onClick={() => void onReviewBranch(task)}>审查改动</button>}
        {task.status === "done" && task.branch_review?.verification_stale && <button className="primary" disabled={taskReviewVerifying} onClick={() => void onVerify(task)}>重新验证</button>}
        {task.status === "done" && task.branch && <button className="primary" disabled={Boolean(taskReviewGateReason(task.branch_review))} title={taskReviewGateReason(task.branch_review) || "创建 Draft PR"} onClick={() => void onOpenPr(task)}>开 Draft PR</button>}
      </div>
    </section>
  );
}
