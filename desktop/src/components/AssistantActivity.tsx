import { useEffect, useRef, useState, type ReactNode } from "react";
import type { FeedItem } from "../lib/activityFeed";
import { ActivityFeed } from "./ActivityFeed";
import { ActivitySourcesNotice } from "./ActivitySourcesNotice";
import { activityCoverage, type ActivitySource } from "../lib/activitySources";
import { startWorkdayClock } from "../lib/assistantWorkdayReads";
import "./AssistantActivity.css";
import { ActivityDetailDialog, type ActivityDetailStep } from "./ActivityDetailDialog";
import { ActivityRecordDialog } from "./ActivityRecordDialog";
import type { GatewayClient } from "../gateway";
import { assistantAttention } from "../lib/assistantAttention";
import type { TeamSummary } from "../types";

/** Show the selected record where the user can see it, including after a long scroll. */
export function AssistantActivityDetail({ title, children, onClose, steps, statusLabel }: {
  title: string; children: ReactNode; onClose: () => void; steps?: ActivityDetailStep[]; statusLabel?: string;
}) {
  return <ActivityDetailDialog title={title} steps={steps} statusLabel={statusLabel} onClose={onClose}>{children}</ActivityDetailDialog>;
}

/**
 * 默认交付待处理、异常与最近结果；普通过程记录由用户主动展开。
 * 来源不全时照实说，不把读不到当成没有。
 */
export function AssistantActivity({ items, currentSession, connected, children, onOpen, sources = [], onRefresh, client, teams = [], exceptionFocus = 0, onExceptionFocused }: {
  items: FeedItem[]; currentSession?: string; connected: boolean;
  children?: ReactNode; onOpen: (item: FeedItem) => void;
  sources?: ActivitySource[]; onRefresh?: () => void;
  client?: Pick<GatewayClient,"getAgentActivityDetail"> | null;
  teams?: TeamSummary[];
  exceptionFocus?: number;
  onExceptionFocused?: () => void;
}) {
  const [, setClock] = useState(() => new Date());
  useEffect(() => startWorkdayClock(setClock, document), []);
  // A newly received snapshot can be newer than the previous clock tick.
  const now = Date.now();
  const coverage = activityCoverage(sources, connected, now);
  const attention = assistantAttention(items, teams);
  const exceptionSection = useRef<HTMLElement>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [requestedExceptions, setRequestedExceptions] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = attention.history.find(item => item.id === selectedId);
  useEffect(() => {
    if (!exceptionFocus) return;
    setShowHistory(false); setSelectedId(null); setRequestedExceptions(true);
    const frame = requestAnimationFrame(() => {
      exceptionSection.current?.scrollIntoView?.({ block: "start" });
      exceptionSection.current?.focus({ preventScroll: true });
      onExceptionFocused?.();
    });
    return () => cancelAnimationFrame(frame);
    // The request number is an explicit navigation event, not a live-update focus rule.
  }, [exceptionFocus]);
  const [openRecords, setOpenRecords] = useState<ReadonlySet<string>>(() => new Set());
  const itemsRef = useRef(items);
  itemsRef.current = items;
  useEffect(() => {
    const present = new Set(items.map(item => item.id));
    setOpenRecords(previous => {
      const retained = new Set([...previous].filter(id => present.has(id)));
      return retained.size === previous.size ? previous : retained;
    });
  }, [items]);
  const lastRead = sources.reduce((latest, source) => Math.max(latest, source.checkedAt ?? 0), 0);
  const lastReadText = lastRead ? new Date(lastRead).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : "";
  const feed = (shown: FeedItem[], grouped = false) => <ActivityFeed items={shown} scope="all" currentSession={currentSession} onOpen={onOpen}
    groupByTime={grouped}
    onInspect={item => { if (item.confirmationId || item.codexActionId || item.dispatchId) onOpen(item); else setSelectedId(item.id); }}
    recordExpansion={{ openIds: openRecords, onToggle: (id, open) => {
      if (!itemsRef.current.some(item => item.id === id)) return;
      setOpenRecords(previous => {
        if (previous.has(id) === open) return previous;
        const next = new Set(previous);
        if (open) next.add(id); else next.delete(id);
        return next;
      });
    } }}
    emptyMessage={!coverage.complete ? "暂未读到这类记录；部分来源待核对，不能据此判断没有事项。" : undefined} />;
  return <section className="assistant-activity" aria-label="助理动态">
    {!connected && <p className="assistant-activity-offline" role="status">
      {lastReadText ? `下面是 ${lastReadText} 读到的动态` : "下面是上次读到的动态"}，连上之后再核对现在的进度。</p>}
    {connected && !coverage.complete && <ActivitySourcesNotice sources={sources} connected={connected} now={now} onRefresh={onRefresh} />}
    {children}
    <div className="assistant-activity-list">
      {showHistory ? feed(attention.history, true) : <>
        {attention.decisions.length > 0 && <section aria-label="等你处理"><h5>等你处理 · {attention.decisions.length}</h5>{feed(attention.decisions)}</section>}
        {(attention.exceptions.length > 0 || requestedExceptions || exceptionFocus > 0) && <section ref={exceptionSection} tabIndex={-1} className="assistant-exceptions" aria-label="需要核对"><h5>需要核对 · {attention.exceptions.length}</h5>
          {attention.exceptions.length > 0 ? feed(attention.exceptions) : <p className="assistant-attention-note">{coverage.complete ? "当前已读取的记录中没有需核对项。" : "来源尚未完整读取，请刷新核对。"}</p>}</section>}
        {attention.recentResults.length > 0 && <section aria-label="最近结果"><h5>最近结果</h5>{feed(attention.recentResults)}
          {attention.additionalResults > 0 && <p className="assistant-attention-note">其余 {attention.additionalResults} 条结果在全部记录中。</p>}</section>}
        {attention.decisions.length === 0 && attention.exceptions.length === 0 && <p className="assistant-attention-note">
          {coverage.complete ? "已读取的动态中暂未发现待处理事项。" : "部分来源待核对，不能据此判断没有事项。"}</p>}
        {attention.running > 0 && <p className="assistant-attention-note">已读取的进行中记录：{attention.running} 条，过程已收起。</p>}
        {attention.teamRecords.length > 0 && <p className="assistant-attention-note">另有 {attention.teamRecords.length} 条小组内部记录，由协调人处理；可在小组或全部记录查看。</p>}
      </>}
      <button className="assistant-attention-toggle" aria-expanded={showHistory} onClick={() => setShowHistory(value => !value)}>
        {showHistory ? "返回重点摘要" : `查看全部记录（${items.length}）`}</button>
    </div>
    {selected && <ActivityRecordDialog key={selected.id} item={selected} client={client} onClose={() => setSelectedId(null)} onNavigate={() => onOpen(selected)} />}
  </section>;
}
