// 侧栏「最近」：按状态分组的会话列表，以及下面的「需要继续」（待决定、进行中的目标和后台任务）。
// 只负责显示和把点击交回 App；会话切换、重命名、删除都由 App 的回调做。
import { PencilLine, Trash2 } from "lucide-react";
import { Fragment } from "react";

import { sessionStatusLabel, statusLabel } from "../lib/labels";
import { groupSessions, liveSessionStatus, sessionTimeLabel } from "../lib/sessionGroups";
import type { DecisionItem, GoalItem, SessionSummary, TaskItem } from "../types";

export function SidebarSessions({
  sessions, activeSid, busy, query, decisions, goals, tasks,
  onSelect, onFocusTask, onOpenHooks, onRename, onDelete, onOpenDecision, onOpenGoals, onOpenTasks,
}: {
  sessions: SessionSummary[];
  activeSid: string;
  busy: boolean;
  /** 侧栏搜索框的内容；空串 = 不筛 */
  query: string;
  decisions: DecisionItem[];
  /** 还没结束的目标 */
  goals: GoalItem[];
  /** 还没结束的后台任务 */
  tasks: TaskItem[];
  onSelect: (sid: string) => void;
  onFocusTask: (taskId: string) => void;
  /** 打开这条会话的 Hook 失败、超时或阻止记录 */
  onOpenHooks: (sid: string) => void;
  onRename: (session: SessionSummary) => void;
  onDelete: (session: SessionSummary) => void;
  onOpenDecision: (decision: DecisionItem) => void;
  onOpenGoals: () => void;
  onOpenTasks: () => void;
}) {
  const keyword = query.trim().toLowerCase();
  const visible = sessions.filter((session) => !keyword || (session.title || "新任务").toLowerCase().includes(keyword));
  const groups = groupSessions(visible, activeSid, busy);
  const renderSession = (session: SessionSummary) => {
    const active = session.sid === activeSid;
    const liveStatus = liveSessionStatus(session, active, busy);
    const backgroundCount = (session.background_tasks?.active ?? 0) + (session.background_tasks?.attention ?? 0);
    // 列表只留标题；元信息只保留需要人动手的（后台任务、待处理、Hook 问题）。
    const hasMetadata = Boolean(backgroundCount || session.hook_issues?.count);
    const showsStatus = ["working", "needs_input", "failed", "queued"].includes(liveStatus);
    const rowTitle = [
      session.running_prompt || session.activity || session.title,
      session.cwd ? `目录：${session.cwd}` : "",
      session.branch ? `分支：${session.branch}` : "",
      session.background_tasks?.latest_prompt ? `后台：${session.background_tasks.latest_prompt}` : "",
      session.hook_issues?.latest ? `Hook：${session.hook_issues.latest.summary}` : "",
    ].filter(Boolean).join("\n");
    return (
      <div
        className={`session-row ${active ? "active" : ""} ${active && busy ? "busy" : ""} ${hasMetadata ? "with-meta" : ""} status-${liveStatus}`}
        key={session.sid}
        onClick={() => onSelect(session.sid)}
        role="button"
        tabIndex={0}
        title={rowTitle}
      >
        <span className="session-status" />
        <div className="session-copy">
          <strong>{session.title || "新任务"}</strong>
          {showsStatus && <span>{sessionStatusLabel(session, active, busy)}</span>}
          {hasMetadata && (
            <div className="session-meta" aria-label="会话运行上下文">
              {(session.background_tasks?.active ?? 0) > 0 && (
                <button
                  className="active"
                  title="打开对应后台任务、计划与 worktree"
                  onClick={(event) => { event.stopPropagation(); onFocusTask(session.background_tasks?.active_task_id ?? session.background_tasks?.latest_task_id ?? ""); }}
                >{session.background_tasks?.active} 后台</button>
              )}
              {(session.background_tasks?.attention ?? 0) > 0 && (
                <button
                  className="attention"
                  title="打开需要处理的后台任务交接"
                  onClick={(event) => { event.stopPropagation(); onFocusTask(session.background_tasks?.attention_task_id ?? session.background_tasks?.latest_task_id ?? ""); }}
                >{session.background_tasks?.attention} 待处理</button>
              )}
              {(session.hook_issues?.count ?? 0) > 0 && (
                <button
                  className="attention"
                  title="定位 Hook 失败、超时或阻止记录"
                  onClick={(event) => { event.stopPropagation(); onOpenHooks(session.sid); }}
                >{session.hook_issues?.count} Hook</button>
              )}
            </div>
          )}
        </div>
        {!showsStatus && <time className="session-time">{sessionTimeLabel(session.updated)}</time>}
        <div className="session-actions">
          <button aria-label="重命名任务" title="重命名" onClick={(event) => { event.stopPropagation(); onRename(session); }}><PencilLine size={13} /></button>
          <button aria-label="删除任务" title="删除" onClick={(event) => { event.stopPropagation(); onDelete(session); }}><Trash2 size={13} /></button>
        </div>
      </div>
    );
  };
  return (
    <div className="session-list">
      {sessions.length === 0 && <div className="session-empty">描述一个目标后，任务线程会出现在这里。</div>}
      {groups.map((group) => (
        <Fragment key={group.key}>
          {groups.length > 1 && <div className={`session-group-label group-${group.key}`}>{group.label} · {group.sessions.length}</div>}
          {group.sessions.map(renderSession)}
        </Fragment>
      ))}
      {(decisions.length > 0 || goals.length > 0 || tasks.length > 0) && (
        <div className="work-queue">
          <div className="work-queue-label">需要继续</div>
          {decisions.slice(0, 2).map((decision) => (
            <button key={decision.id} onClick={() => onOpenDecision(decision)}>
              <span className={`queue-dot severity-${decision.severity}`} />
              <span><strong>{decision.title}</strong><small>{decision.detail}</small></span>
              <b>处理</b>
            </button>
          ))}
          {goals.slice(0, 2).map((goal) => (
            <button key={goal.id} onClick={onOpenGoals}>
              <span className={`queue-dot status-${goal.status}`} />
              <span><strong>{goal.objective}</strong><small>{goal.progress.passed}/{goal.progress.total} 条完成标准</small></span>
              <b>{statusLabel(goal.status)}</b>
            </button>
          ))}
          {tasks.slice(0, 3).map((task) => (
            <button key={task.id} onClick={onOpenTasks}>
              <span className={`queue-dot status-${task.status}`} />
              <span><strong>{task.prompt || task.plan?.task || "后台工作"}</strong><small>{task.branch || task.id}</small></span>
              <b>{statusLabel(task.status)}</b>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
