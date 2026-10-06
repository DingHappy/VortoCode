import { useEffect, useState } from "react";
import type { GatewayClient } from "../gateway";
import type { TaskItem, TaskCoverageReport } from "../types";
import { dispatchTaskIdentity } from "../lib/taskDispatch";
import { coverageMatches, coverageLabel, coverageReasons } from "../lib/taskCoverage";
import { useTaskAction } from "../hooks/useTaskAction";
import { TaskCoverageRecords } from "./TaskCoverageRecords";

type Props = { task: TaskItem; getClient: () => GatewayClient | null; onUpdated: () => void };
export function TaskCoverageActions(props: Props) {
  if (!dispatchTaskIdentity(props.task)) return null;
  return <CoverageForm key={`${props.task.id}:${props.task.owner_session}:${props.task.collaboration?.round}`} {...props} />;
}

function CoverageForm({ task, getClient, onUpdated }: Props) {
  const identity = dispatchTaskIdentity(task)!;
  const client = getClient();
  const { busy, error, run } = useTaskAction(client, getClient, onUpdated, { repeatable: true });
  const [report, setReport] = useState<TaskCoverageReport | null>(null);
  const [requestId, setRequestId] = useState<string>(() => crypto.randomUUID());
  useEffect(() => { setReport(null); setRequestId(crypto.randomUUID()); }, [client]);

  function advance(restart = false) {
    const id = restart ? crypto.randomUUID() : report?.request_id || requestId;
    void run(async (client, isCurrent) => {
      if (restart) { setRequestId(id); setReport(null); }
      const result = await client.scanTaskDependencies(task.id, identity.session, identity.round, id,
        restart ? null : report?.next_cursor ?? null, restart ? 512 : report?.page_size ?? 512);
      if (!isCurrent()) return;
      if (!coverageMatches(result, task.id, identity.round, id) || (!restart && report && (
        result.scan_id !== report.scan_id || result.snapshot_id !== report.snapshot_id
        || result.sequence < report.sequence || result.sequence > report.sequence + 1))) {
        throw new Error("未收到匹配的覆盖记录，状态未知，请检查后重试。");
      }
      setReport(result);
    });
  }
  function inspect(resume = false) {
    void run(async (client, isCurrent) => {
      const result = await client.taskDependencyScanStatus(task.id, identity.session, identity.round,
        resume ? undefined : report?.scan_id);
      if (!isCurrent()) return;
      if (!result) throw new Error("没有可恢复的核对记录。");
      if (!coverageMatches(result, task.id, identity.round, resume ? undefined : report?.request_id)
          || (!resume && report && (result.scan_id !== report.scan_id || result.snapshot_id !== report.snapshot_id))) {
        throw new Error("覆盖记录不匹配，状态未知，请刷新任务。");
      }
      setReport(result); setRequestId(result.request_id);
    });
  }
  return <details className="task-dependency-actions task-coverage-actions">
    <summary>下游覆盖核对</summary>
    <strong role="status">{error ? "覆盖状态未知" : report ? coverageLabel(report) : "尚未核对下游覆盖"}</strong>
    {report && <>
      <p>记录读取 {report.coverage.entries_read}/{report.coverage.entries_total} · {report.coverage.graph_complete
        ? `下游核对 ${report.coverage.reconciled}/${report.coverage.selected}`
        : report.coverage.selected ? `已发现至少 ${report.coverage.selected} 项，已核对 ${report.coverage.reconciled} 项；范围未完整确认`
          : "下游范围尚未完整确认"}</p>
      {coverageReasons(report).map((reason, index) => <p key={index}>{reason}</p>)}
      <small>上次观察：{report.observed_at}；继续或检查时会重新确认快照有效性。</small>
    </>}
    <p>覆盖核对与任务停止、结果验收及交接处理分别记录。失效任务先保存证据再请求停止。</p>
    <div className="task-actions">
      {!report && <button disabled={!client || busy} onClick={() => advance()}>开始分页核对</button>}
      {report?.next_cursor && <button disabled={!client || busy} onClick={() => advance()}>继续下一页</button>}
      {report && <button disabled={!client || busy} onClick={() => inspect()}>检查覆盖有效性</button>}
      {!report && <button disabled={!client || busy} onClick={() => inspect(true)}>恢复上次核对</button>}
      {report && <button disabled={!client || busy} onClick={() => advance(true)}>重新开始核对</button>}
    </div>
    {busy && <small>核对中…</small>}
    <TaskCoverageRecords id={task.id} session={identity.session} owner={task.owner_session!} client={client}
      busy={busy} run={run} onReleased={scanId => {
        if (report?.scan_id === scanId) { setReport(null); setRequestId(crypto.randomUUID()); }
      }} onObserved={observed => {
        if (report?.scan_id === observed.scan_id && report.snapshot_id === observed.snapshot_id && report.sequence === observed.sequence)
          setReport(observed);
      }} />
    {error && <div role="alert" className="task-error">{error}</div>}
  </details>;
}
