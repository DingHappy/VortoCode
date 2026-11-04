import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUpRight, CheckCircle2, CircleHelp, ListChecks, LoaderCircle, RefreshCw } from "lucide-react";
import type { GatewayClient } from "../gateway";
import type { AssistantDispatch, AssistantScheduleItem, CodexAction, CodexProgressSnapshot, MemoryProposal, PendingConfirmation, PersonalGoal, SessionSummary, DesktopRuntimeInbox, NoticeItem, OpsHeartbeat } from "../types";
import { buildAssistantWorkday, type AssistantWorkday as Workday, type WorkdayBucket, type WorkdayItem, type WorkdaySource, type WorkdayTarget } from "../lib/assistantWorkday";
import { assistantWorkdayReads, startWorkdayClock } from "../lib/assistantWorkdayReads";
import { startPolling } from "../lib/polling";
import { CodexActionCard } from "./CodexActionCard";
import { ResidentComputerCard } from "./ResidentComputerCard";
import { CodexGoalProgress } from "./CodexGoalProgress";
import { codexGoalProgress, codexProgressFresh } from "../lib/codexGoalSupervision";
import { freshAt } from "../lib/readFreshness";
import "./AssistantWorkday.css";

const BUCKETS = [
  { id: "today", label: "今天的事", icon: ListChecks },
  { id: "progress", label: "正在推进", icon: LoaderCircle },
  { id: "completed", label: "已核实完成", icon: CheckCircle2 },
  { id: "decisions", label: "等你决定", icon: CircleHelp },
] as const;
const STATE: Record<WorkdayItem["state"], string> = {
  running: "推进中", decision: "等你决定", review: "待验收", verified: "已确认", unknown: "待核对",
};

export function AssistantWorkdayView({ model, now, busy = false, connected, onRefresh, onOpen }: {
  model: Workday; now: Date; busy?: boolean; connected: boolean;
  onRefresh: () => void; onOpen: (target: WorkdayTarget) => void;
}) {
  const [bucket, setBucket] = useState<WorkdayBucket>("today");
  const [expanded, setExpanded] = useState(false);
  const items = model[bucket], shown = expanded ? items : items.slice(0, 5);
  return <section className="assistant-workday" aria-label="今日工作">
    <header className="workday-heading"><div><span>你的工作简报</span><h3>今日工作</h3>
      <time dateTime={now.toISOString()}>{now.toLocaleDateString(undefined, { month: "long", day: "numeric", weekday: "long" })}</time></div>
      <button aria-label="刷新今日工作" title="核对最新进展" disabled={!connected || busy} onClick={onRefresh}>
        <RefreshCw size={15} className={busy ? "session-step-spin" : ""} /></button>
    </header>
    <div className="workday-counts" aria-label="工作概览">
      {BUCKETS.map(({ id, label, icon: Icon }) => <button key={id} aria-pressed={bucket === id}
        className={bucket === id ? "active" : ""} onClick={() => { setBucket(id); setExpanded(false); }}>
        <Icon size={15} aria-hidden="true" /><span>{label}</span><strong>{model[id].length}</strong>
      </button>)}
    </div>
    <p className="workday-explainer">{bucket === "today" ? "包含今天更新的事项，以及此前尚未结束的目标和任务。"
      : bucket === "progress" ? "看当前进展与下一步；目标推进不代表正在自动执行。"
      : bucket === "completed" ? "这里只计入达标条件逐项有通过记录的目标。执行结束、测试通过仍需核对交付。"
      : "查看正文与依据后，再进入原有任务单或目标确认。"}</p>
    {model.warnings.length > 0 && <details className="workday-warnings" open={!connected}>
      <summary>{connected ? "部分来源待核对，数量仅代表已读取的记录" : "连接中断，当前进展待核对"}</summary>
      <ul>{model.warnings.map(text => <li key={text}>{text}</li>)}</ul>
    </details>}
    {shown.length ? <ul className="workday-items">{shown.map(item => <li key={item.id}>
      <button className={`workday-item ${item.state}`} onClick={() => onOpen(item.target)} aria-label={`查看${item.title}`}>
        <span className="workday-item-top"><small>{item.source}</small><span>{STATE[item.state]}</span></span>
        <strong>{item.title}</strong><span className="workday-item-detail">{item.detail}</span>
        <span className="workday-item-link">{item.target.kind === "goal" ? "查看目标与验收" : item.target.kind === "dispatch" ? "查看任务单" : item.target.kind === "confirmation" ? "查看原确认卡"
          : item.target.kind === "schedule" ? "查看定时与运行记录" : item.target.kind === "memory" ? "去确认记忆"
          : item.target.kind === "session" ? "打开对话" : item.target.kind === "project" ? "去工作台任务"
          : item.target.kind === "ops" ? "查看报到详情" : item.target.kind === "notice" ? "查看全文" : "查看进度与建议"}<ArrowUpRight size={13} /></span>
      </button>
    </li>)}</ul> : <p className="workday-empty">{model.warnings.length ? !connected
      ? "暂未取得可展示的记录；恢复连接后再核对。"
      : busy ? "正在核对来源，请稍候；当前列表可能不完整。" : "部分来源尚未取得最新状态，请刷新核对。"
      : bucket === "completed" ? "今天还没有验收记录齐全的完成目标。"
      : bucket === "decisions" ? "已读取的事项中，暂时没有需要你决定的事。"
      : "已读取的记录中，暂时没有这类事项。"}</p>}
    {items.length > 5 && <button className="workday-more" onClick={() => setExpanded(!expanded)}>
      {expanded ? "收起列表" : `查看其余 ${items.length - 5} 项`}</button>}
  </section>;
}

export function AssistantWorkday({ client, scope, connected, resident = false, dispatches, dispatchesLoaded, codexActions, confirmation, onConfirmation, onDispatch, onGoals, onSchedule, onMemory, onSession, activeSid, mainSid, projectSources, onProjects, ops: withOps = false, onRefreshTasks, onCodexDecision }: {
  client: (Pick<GatewayClient, "listPersonalGoals" | "getCodexProgress">
    & Partial<Pick<GatewayClient, "getResidentComputer" | "getAssistantSchedule" | "listMemoryProposals" | "listSessions" | "getOpsHeartbeats" | "listNotices">>) | null;
  scope: string; connected: boolean; resident?: boolean; dispatches: AssistantDispatch[];
  dispatchesLoaded: boolean; codexActions: CodexAction[];
  confirmation?: PendingConfirmation | null; onConfirmation?: () => void;
  onDispatch: (id: string) => void; onGoals: (id: string) => void; onRefreshTasks: () => Promise<void>;
  /** 打开「定时」标签；不给就不读定时来源 */
  onSchedule?: (id: string) => void;
  /** 打开身份里的记忆确认；不给就不读候选记忆 */
  onMemory?: () => void;
  /** 切到某个对话；不给就不读对话列表 */
  onSession?: (sid: string) => void;
  activeSid?: string; mainSid?: string;
  /** 各项目运行端的工作快照；onProjects 打开工作台「任务」页 */
  projectSources?: DesktopRuntimeInbox[]; onProjects?: () => void;
  /** 读运维报到和自愈记录（只在常驻助理上有意义）；详情在概览里原地展开 */
  ops?: boolean;
  onCodexDecision: (action: CodexAction, decision: "approve" | "cancel") => Promise<void>;
}) {
  const owner = useMemo(() => ({ client, scope }), [client, scope]);
  const identity = useRef({ owner, connected });
  identity.current = { owner, connected };
  const [goalSnapshot, setGoals] = useState<{ owner: typeof owner; source: WorkdaySource<PersonalGoal[]> } | null>(null);
  const [codexSnapshot, setCodex] = useState<{ owner: typeof owner; source: WorkdaySource<CodexProgressSnapshot | null> } | null>(null);
  const [scheduleSnapshot, setSchedule] = useState<{ owner: typeof owner; source: WorkdaySource<AssistantScheduleItem[]> } | null>(null);
  const [proposalSnapshot, setProposals] = useState<{ owner: typeof owner; source: WorkdaySource<MemoryProposal[]> } | null>(null);
  const [sessionSnapshot, setSessions] = useState<{ owner: typeof owner; source: WorkdaySource<SessionSummary[]> } | null>(null);
  const [opsSnapshot, setOps] = useState<{ owner: typeof owner; source: WorkdaySource<OpsHeartbeat[]> } | null>(null);
  const [noticeSnapshot, setNotices] = useState<{ owner: typeof owner; source: WorkdaySource<NoticeItem[]> } | null>(null);
  const withSchedule = Boolean(onSchedule), withMemory = Boolean(onMemory), withSessions = Boolean(onSession);
  const [now, setNow] = useState(() => new Date());
  const [busy, setBusy] = useState(false);
  const [selection, setSelected] = useState<{ owner: typeof owner; target: WorkdayTarget } | null>(null);
  const selected = selection?.owner === owner ? selection.target : null;
  const refreshRef = useRef<(() => Promise<void>) | null>(null);
  useEffect(() => startWorkdayClock(setNow, document), []);
  useEffect(() => {
    setBusy(false);
    if (!client || !connected) return;
    let active = true;
    const current = () => active && identity.current.owner === owner && identity.current.connected;
    const reads = assistantWorkdayReads({ client: () => identity.current.owner.client, current,
      goals: source => { setGoals({ owner, source }); setNow(new Date()); },
      codex: source => { setCodex({ owner, source }); setNow(new Date()); },
      schedule: withSchedule ? source => { setSchedule({ owner, source }); setNow(new Date()); } : undefined,
      proposals: withMemory ? source => { setProposals({ owner, source }); setNow(new Date()); } : undefined,
      sessions: withSessions ? source => { setSessions({ owner, source }); setNow(new Date()); } : undefined,
      ops: withOps ? source => { setOps({ owner, source }); setNow(new Date()); } : undefined,
      notices: withOps ? source => { setNotices({ owner, source }); setNow(new Date()); } : undefined });
    const refresh = async () => {
      if (!current()) return;
      setNow(new Date()); setBusy(true);
      try { await reads.refresh(); }
      finally { if (current()) { setNow(new Date()); setBusy(false); } }
    };
    refreshRef.current = refresh;
    const stop = startPolling(refresh, { intervalMs: 30_000, hiddenIntervalMs: 120_000, immediate: true, visibility: document });
    return () => { active = false; stop(); reads.invalidate(); if (refreshRef.current === refresh) refreshRef.current = null; };
  }, [owner, client, connected, withSchedule, withMemory, withSessions, withOps]);
  const goals = goalSnapshot?.owner === owner ? goalSnapshot.source : { status: "loading" as const, data: [] };
  const codex = codexSnapshot?.owner === owner ? codexSnapshot.source : { status: "loading" as const, data: null };
  const schedule = !withSchedule ? undefined : scheduleSnapshot?.owner === owner ? scheduleSnapshot.source : { status: "loading" as const, data: [] };
  const proposals = !withMemory ? undefined : proposalSnapshot?.owner === owner ? proposalSnapshot.source : { status: "loading" as const, data: [] };
  const ops = !withOps ? undefined : opsSnapshot?.owner === owner ? opsSnapshot.source : { status: "loading" as const, data: [] };
  const notices = !withOps ? undefined : noticeSnapshot?.owner === owner ? noticeSnapshot.source : { status: "loading" as const, data: [] };
  const sessions = !withSessions ? undefined : sessionSnapshot?.owner === owner ? sessionSnapshot.source : { status: "loading" as const, data: [] };
  const model = buildAssistantWorkday({ dispatches, dispatchesLoaded, codexActions, confirmation, goals, codex, schedule, proposals,
    sessions, activeSid, mainSid, projectSources, ops, notices, connected, now });
  const action = selected?.kind === "codex_action" ? codexActions.find(item => item.id === selected.id) : null;
  const thread = selected?.kind === "codex" ? codex.data?.threads.find(item => item.id === selected.id) : null;
  const goalProgress = codexGoalProgress({ snapshot: codex.data, goals: goals.data,
    codexCurrent: codexProgressFresh(codex, connected, now.getTime()),
    goalsCurrent: connected && goals.status === "current" && freshAt(goals.checkedAt, now.getTime()), now: now.getTime() })
    .items.find(item => item.threadId === thread?.id);
  const opsRow = selected?.kind === "ops" ? ops?.data.find(item => item.source === selected.id) : null;
  const notice = selected?.kind === "notice" ? notices?.data.find(item => JSON.stringify([item.ts, item.source, item.text]) === selected.id) : null;
  return <>
    <AssistantWorkdayView model={model} now={now} connected={connected} busy={busy}
      onRefresh={() => { void refreshRef.current?.(); void onRefreshTasks(); }}
      onOpen={target => { if (target.kind === "dispatch") onDispatch(target.id);
        else if (target.kind === "goal") onGoals(target.id); else if (target.kind === "confirmation") onConfirmation?.();
        else if (target.kind === "schedule") onSchedule?.(target.id); else if (target.kind === "memory") onMemory?.();
        else if (target.kind === "session") onSession?.(target.id); else if (target.kind === "project") onProjects?.();
        else setSelected({ owner, target }); }} />
    <ResidentComputerCard client={client} scope={scope} connected={connected} resident={resident} now={now.getTime()} />
    {action && <CodexActionCard key={action.id} action={action} connected={connected}
      onDecide={decision => onCodexDecision(action, decision)} />}
    {thread && <article className="assistant-task-card workday-codex-detail" aria-label={`Codex 进度：${thread.name}`}>
      <header><strong>{thread.name}</strong></header>
      {thread.cwd && <p>{thread.cwd}</p>}
      <p>最近报告：{codex.data?.received_at ? new Date(codex.data.received_at).toLocaleString() : "尚无报告"}</p>
      <p>{thread.turn?.request || "尚无任务说明"}</p>
      {thread.turn?.reply && <details><summary>Codex 最近回复</summary><p>{thread.turn.reply}</p></details>}
      <p>等待批准或回答时，请到 Codex 处理；此处的报告不替代交付验收。</p>
      {goalProgress && <CodexGoalProgress progress={goalProgress} onOpenGoal={id => { setSelected(null); onGoals(id); }} />}
    </article>}
    {opsRow && <article className="assistant-task-card workday-codex-detail" aria-label={`运维报到：${opsRow.source}`}>
      <header><strong>运维 · {opsRow.source}</strong></header>
      <p>{opsRow.status === "failed" ? `最近一次报失败：${opsRow.text || "没有附带原因"}` : opsRow.status === "overdue" ? "超过约定间隔没有报到成功" : "报到正常"}</p>
      <p>上次报到：{opsRow.last_report ? new Date(opsRow.last_report).toLocaleString() : "没有记录"}；上次成功：{opsRow.last_ok ? new Date(opsRow.last_ok).toLocaleString() : "没有记录"}；约定每 {opsRow.expect_hours} 小时内一次。</p>
      <p>要到出问题的那台机器上处理；它下次报到成功后，这条会自己消失。</p>
    </article>}
    {notice && <article className="assistant-task-card workday-codex-detail" aria-label="自愈记录">
      <header><strong>自动修复没有成功</strong></header>
      <p>{notice.ts ? new Date(notice.ts).toLocaleString() : ""} · {notice.source}</p>
      <p style={{ whiteSpace: "pre-wrap" }}>{notice.text}</p>
    </article>}
    {selected && <button className="workday-more" onClick={() => setSelected(null)}>收起详情</button>}
  </>;
}
