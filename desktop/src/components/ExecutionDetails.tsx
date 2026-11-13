import { readTaskExecutionObservation, type ExecutionObservation } from '../lib/executionObservation';

const states: Record<ExecutionObservation['state'], string> = {
  queued: '待开始', running: '在忙', waiting_user: '等回答或确认', finished: '本次执行已结束',
  failed: '本次执行失败', cancelled: '本次执行已取消', unknown: '无法确认',
};

/** Read-only task-bound observation. No actions, approvals or inferred acceptance. */
export function ExecutionDetails({ taskId, value, current }: { taskId: string; value: unknown; current: boolean }) {
  const report = readTaskExecutionObservation(value, taskId);
  const snapshot = report?.snapshot;
  const state = current && snapshot ? snapshot.state : 'unknown';
  const date = snapshot ? new Date(snapshot.observed_at * 1000) : null;
  const observed = date && Number.isFinite(date.getTime()) ? date.toISOString() : '未知';
  return <details className="staff-execution">
    <summary>执行观察：{states[state]}</summary>
    <div className="staff-section">
      <p className="settings-card-note">一次执行结束不代表任务验收通过；任务和验收以原记录为准。</p>
      {!report && <p>没有可用的执行报告。旧记录、缺失或不兼容的报告保持未知。</p>}
      {report && snapshot && <>
        {(!current || report.stale) && <p>这是上次执行的观察，当前情况需要核对原任务。</p>}
        {snapshot.progress.summary && <p>{snapshot.progress.summary}</p>}
        <dl className="staff-grants">
          <dt>来源</dt><dd>{snapshot.ref.backend_id}</dd>
          <dt>任务轮次</dt><dd>第 {report.round} 轮</dd>
          <dt>观察时间（UTC）</dt><dd>{observed}</dd>
        </dl>
        <details><summary>执行身份</summary><pre className="staff-record">{JSON.stringify(snapshot.ref, null, 2)}</pre></details>
      </>}
    </div>
  </details>;
}
