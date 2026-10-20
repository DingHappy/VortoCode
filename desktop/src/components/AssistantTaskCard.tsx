import { useRef, useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import type { AssistantDispatch } from "../types";
import { assistantTaskActions, progressLine, type AssistantTaskAction } from "../lib/assistantTask";

const labels: Record<AssistantDispatch["status"], string> = {
  proposed: "等待确认下发", sending: "正在下发", submission_unknown: "提交结果待核对",
  send_failed: "下发失败", running: "执行中", waiting: "等待处理", done: "执行结束，等待审查",
  failed: "执行失败", cancelled: "已撤回", stopped: "已停下",
};

type Props = {
  task: AssistantDispatch;
  connected: boolean;
  /** ``answer``：action 为 "answer" 时，人在卡片上填的回答 */
  onAction: (action: AssistantTaskAction, answer?: string) => Promise<void>;
};

/** Buttons invoke fixed application actions, never instructions from model text. */
export function AssistantTaskCard({ task, connected, onAction }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [answer, setAnswer] = useState("");
  const pending = useRef(false);
  const identity = useRef(task.id);
  identity.current = task.id;
  const actions = assistantTaskActions(task);
  // PR 地址来自项目运行时：只把 https 地址当链接
  const prLink = task.pr_url && /^https:\/\//.test(task.pr_url) ? task.pr_url : "";
  const act = async (action: Parameters<Props["onAction"]>[0], text?: string) => {
    if (pending.current || !connected) return;
    const id = task.id;
    pending.current = true;
    setBusy(true); setError("");
    try { await onAction(action, text); if (action === "answer" && identity.current === id) setAnswer(""); }
    catch (reason) { if (identity.current === id) setError(reason instanceof Error ? reason.message : "操作失败，请核对状态"); }
    finally { pending.current = false; if (identity.current === id) setBusy(false); }
  };
  // 等确认的任务单：按钮紧跟在状态下面（目标和验收标准可能很长，别让人滚到底才找得到「确认下发」）
  const actionRow = (
    <div className="task-actions">
      {actions.approve && <button disabled={busy || !connected} onClick={() => void act("approve")}>确认下发</button>}
      {actions.cancel && <button disabled={busy || !connected} onClick={() => void act("cancel")}>撤回任务单</button>}
      {actions.retry && <button disabled={busy || !connected} onClick={() => void act("approve")}>继续核对并提交</button>}
      {task.remote_task_id && <button disabled={busy || !connected} onClick={() => void act("refresh")}>核对状态</button>}
      {actions.confirmGoal && <button disabled={busy || !connected} onClick={() => void act("confirm_goal")}>算达成这条目标标准</button>}
      {actions.openPr && <button disabled={busy || !connected} onClick={() => void act("open_pr")}>开 draft PR</button>}
      {actions.open && <button disabled={busy || !connected} onClick={() => void act("open")}>{task.status === "done" && task.branch ? "查看交付与审查" : "打开项目任务"}</button>}
    </div>
  );
  return <article className="assistant-task-card" aria-label={`任务单：${task.title}`}>
    <header><strong>{task.title}</strong><span>{task.project}</span></header>
    <p className="assistant-task-status">{labels[task.status] ?? "状态未知"}</p>
    {task.sync_state === "unreachable" && <p role="status">当前无法核实，保留上次执行状态。{task.sync_error}</p>}
    {task.last_checked_at && <small>上次核对：{new Date(task.last_checked_at).toLocaleString()}</small>}
    {task.goal_link && <p className="assistant-task-goal">为目标「{task.goal_link.objective}」
      {task.goal_link.criterion ? `· 验收标准：${task.goal_link.criterion}` : ""}
      {task.goal_confirmed_at ? "（你已确认达成）" : ""}</p>}
    {task.attempt && task.attempt > 1 && <small>第 {task.attempt} 次尝试{task.retry_of ? `（接着 ${task.retry_of}）` : ""}</small>}
    {task.status === "proposed" && task.retry_note && <p>这次换个做法：{task.retry_note}</p>}
    {task.status === "proposed" && actionRow}
    {task.progress && task.status !== "proposed" && <section className="assistant-task-progress" aria-label="流水线进度">
      <p>{progressLine(task)}</p>
      {task.progress.steps.length > 0 && <ol>{task.progress.steps.map((step, i) => <li key={i} data-status={step.status}>
        {({ landed: "✓", running: "▶", failed: "✗", pending: "·" })[step.status]} {step.title || `第 ${i + 1} 步`}</li>)}
        {task.progress.more > 0 && <li>…还有 {task.progress.more} 步</li>}</ol>}
    </section>}
    {task.status === "waiting" && task.question && <section className="assistant-task-question" aria-label="任务在问的问题">
      <p><strong>任务在问你：</strong></p>
      <p className="assistant-task-text">{task.question.question}</p>
      {task.question.context && <details><summary>它查到的情况</summary><p className="assistant-task-text">{task.question.context}</p></details>}
      {actions.answer ? <>
        {task.question.options.length > 0 && <div className="task-actions">
          {task.question.options.map((option, i) => <button key={i} disabled={busy || !connected}
            onClick={() => setAnswer(option)}>{option}</button>)}
        </div>}
        <textarea aria-label="你的回答" value={answer} maxLength={2000} rows={2} disabled={busy || !connected}
          placeholder="点上面的建议，或直接写你的回答" onChange={event => setAnswer(event.target.value)} />
        <div className="task-actions">
          <button disabled={busy || !connected || !answer.trim()} onClick={() => void act("answer", answer.trim())}>交给任务接着做</button>
        </div>
        {task.question.expires_at && <small>{new Date(task.question.expires_at).toLocaleString()} 前有效</small>}
      </> : <small>这张单是旧方式下发的，请打开项目任务回答。</small>}
    </section>}
    <details open={task.status === "proposed"}>
      <summary>目标与验收标准</summary>
      <p className="assistant-task-text">{task.goal}</p>
      {task.criteria.length > 0 && <ul>{task.criteria.map((line, i) => <li key={i}>{line}</li>)}</ul>}
    </details>
    {task.tainted && <p className="assistant-task-warning">任务单包含外部内容，请确认目标和范围符合你的要求。</p>}
    {task.branch && <p>交付分支：<code>{task.branch}</code></p>}
    {prLink && <p>draft PR：<a href={prLink} rel="noopener noreferrer"
      onClick={event => { event.preventDefault(); void openUrl(prLink); }}>{prLink}</a>（合并请在 GitHub 上审完再点）</p>}
    {!task.pr_url && task.pr_state === "opening" && <p role="status">正在推分支、开 draft PR，稍后点“核对状态”看结果。</p>}
    {!task.pr_url && task.pr_state === "failed" && <p className="task-error">没开成 PR：{task.pr_error || "原因未知"}</p>}
    {task.branch && <section className="assistant-delivery-evidence" aria-label="交付验证证据">
      {task.delivery_evidence ? <>
        <p>核对提交：<code>{task.delivery_evidence.head.slice(0, 12)}</code></p>
        <p>{({ passed: "该提交验证通过", failed: "该提交验证失败", stale: "验证证据已过期，需要重新验证", unknown: "尚无该提交的验证证据" })[task.delivery_evidence.verification]}</p>
        {task.delivery_evidence.coverage_known && <p>文本变更审查：{task.delivery_evidence.accepted_hunks}/{task.delivery_evidence.total_hunks} 已接受</p>}
        <small>证据核对：{new Date(task.delivery_evidence.checked_at).toLocaleString()}</small>
      </> : <p>尚未核对交付证据，点击“核对状态”获取当前提交与审查情况。</p>}
      {task.delivery_error && <p role="status">当前无法核实交付证据：{task.delivery_error}</p>}
    </section>}
    {task.outcome?.kind === "acceptance" && task.outcome.criteria && task.outcome.criteria.length > 0 &&
      <section className="assistant-task-acceptance" aria-label="验收对照">
        <p>验收对照（助理看输出判断，不代表人工验收）：{task.outcome.criteria.filter(row => row.verdict === "met").length}/{task.outcome.criteria.length} 条对上</p>
        <ul>{task.outcome.criteria.map((row, i) => <li key={i}>
          {({ met: "✓", unmet: "✗", unknown: "?" })[row.verdict]} {row.text}{row.evidence ? `——${row.evidence}` : ""}</li>)}</ul>
      </section>}
    {task.outcome?.kind === "diagnosis" && <section aria-label="失败复盘">
      <p>助理的判断：{task.outcome.diagnosis}</p>
      {task.outcome.draft_id ? <small>已拟好下一次的任务单（换个做法：{task.outcome.change}），在任务单列表里等你确认。</small>
        : task.outcome.capped ? <small>这件事已经试了好几次，建议换个做法或拆小再派。</small>
        : <small>换个做法也难成，需要你拿主意：告诉助理想怎么改。</small>}
    </section>}
    {task.result && <details><summary>执行结果</summary><p className="assistant-task-text">{task.result}</p></details>}
    {task.error && <p className="task-error">{task.error}</p>}
    {task.recovery_task && <section aria-label="恢复任务核对">
      {task.sync_state === "unreachable" && <p role="status">恢复信息为上次记录，当前无法重新核实。</p>}
      <p>已核对恢复任务：<code>{task.recovery_task.id}</code></p>
      <p>上次核对的恢复进度：{({ queued: "排队中", running: "执行中", blocked: "等待处理", done: "执行结束，等待审查",
        failed: "执行失败", paused: "已暂停", interrupted: "已中断", cancelled: "已取消" } as Record<string, string>)[task.recovery_task.status] ?? "状态未知"}</p>
      <small>核对时间：{new Date(task.recovery_task.checked_at).toLocaleString()}</small>
      {task.recovery_task.branch && <p>恢复分支：<code>{task.recovery_task.branch}</code></p>}
      {task.recovery_task.error && <p className="task-error">{task.recovery_task.error}</p>}
      {task.recovery_task.result && <details><summary>恢复执行结果</summary><p className="assistant-task-text">{task.recovery_task.result}</p></details>}
      {task.recovery_task.delivery_evidence && <p>恢复提交 <code>{task.recovery_task.delivery_evidence.head.slice(0, 12)}</code>：
        {({ passed: "该提交验证通过", failed: "该提交验证失败", stale: "验证证据已过期", unknown: "验证情况未知" })[task.recovery_task.delivery_evidence.verification]}</p>}
      <small>原任务记录保留；恢复执行结束仍需审查。</small>
      {task.recovery_task.has_further_recovery && <p>该任务还有后续恢复，请打开项目查看完整恢复链。</p>}
    </section>}
    {task.recovery_error && <p role="status">恢复任务当前无法完全核实：{task.recovery_error}</p>}
    {task.status === "done" && <small>执行结束不代表验收通过，请在项目工作台核对 diff 和验证证据。</small>}
    {task.status !== "proposed" && actionRow}
    {!connected && <small>连接中断，恢复后可继续处理。</small>}
    {busy && <small role="status">正在处理…</small>}
    {error && <p role="alert" className="task-error">{error}</p>}
  </article>;
}
