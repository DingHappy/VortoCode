import { useEffect } from "react";
import type { ComponentProps } from "react";
import type { TaskItem, WorktreeWorkspaceSnapshot } from "../types";
import { TaskCard } from "../components/TaskCard";
import { TaskDispatchForm } from "../components/TaskDispatchForm";

type TasksPanelProps = {
  tasks: TaskItem[];
  focusedTaskId: string;
  worktreeWorkspace: WorktreeWorkspaceSnapshot;
  dispatchForm: ComponentProps<typeof TaskDispatchForm>;
  cardActions: Omit<ComponentProps<typeof TaskCard>, "task" | "focused">;
  onFocusTask: (id: string) => void;
};

/** Presentation only: server snapshots and domain actions come from the owner. */
export function TasksPanel({ tasks, focusedTaskId, worktreeWorkspace, dispatchForm,
  cardActions, onFocusTask }: TasksPanelProps) {
  useEffect(() => {
    if (!focusedTaskId) return;
    const frame = window.requestAnimationFrame(() => {
      document.getElementById(`task-card-${focusedTaskId}`)?.scrollIntoView({
        behavior: "smooth", block: "nearest",
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [focusedTaskId, tasks]);

  return (
    <div className="tasks-panel">
      <TaskDispatchForm {...dispatchForm} />
      {(worktreeWorkspace.worktrees.length > 0 || worktreeWorkspace.plans.length > 0) && (
        <section className="worktree-workspace">
          <div className="worktree-workspace-head">
            <strong>Worktree 会话</strong>
            <span>{worktreeWorkspace.worktrees.length} 个实时 · {worktreeWorkspace.plans.length} 个持久计划</span>
          </div>
          {worktreeWorkspace.worktrees.map((worktree) => (
            <button
              className="worktree-live"
              disabled={!worktree.task_id}
              key={worktree.id}
              onClick={() => onFocusTask(worktree.task_id ?? "")}
              title={worktree.task_id ? `定位任务 ${worktree.task_id}` : "未绑定到后台任务的临时 worktree"}
            >
              <i />
              <div>
                <b>{worktree.id}</b>
                <small>{worktree.branch || "detached"} · {worktree.head}</small>
                {worktree.task_id && <small>task {worktree.task_id.slice(0, 10)} · {worktree.plan_id || "计划生成中"}</small>}
              </div>
              <span>{worktree.changed_files} 文件改动</span>
            </button>
          ))}
          {worktreeWorkspace.plans.length > 0 && (
            <details className="worktree-plans">
              <summary>持久计划与恢复点</summary>
              {worktreeWorkspace.plans.slice(0, 8).map((planSession) => {
                const progress = planSession.progress;
                return (
                  <div
                    className={`worktree-plan-row ${tasks.some((task) => task.plan_id === planSession.plan_id) ? "linked" : ""}`}
                    key={planSession.plan_id}
                    onClick={() => onFocusTask(tasks.find((task) => task.plan_id === planSession.plan_id)?.id ?? "")}
                    role={tasks.some((task) => task.plan_id === planSession.plan_id) ? "button" : undefined}
                  >
                    <div><b>{planSession.task}</b><small>{planSession.plan_id} · {planSession.status}</small></div>
                    <span>{progress.landed}/{progress.total}</span>
                  </div>
                );
              })}
            </details>
          )}
        </section>
      )}
      {tasks.length === 0 && <div className="panel-empty compact"><strong>暂无后台任务</strong><p>可下派只读研究，或在隔离分支中执行开发。</p></div>}
      {tasks.map((task) => (
        <TaskCard key={task.id} task={task} focused={focusedTaskId === task.id} {...cardActions} />
      ))}
    </div>
  );
}
