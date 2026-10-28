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

/** Show the selected record where the user can see it, including after a long scroll. */
export function AssistantActivityDetail({ title, children, onClose, steps, statusLabel }: {
  title: string; children: ReactNode; onClose: () => void; steps?: ActivityDetailStep[]; statusLabel?: string;
}) {
  return <ActivityDetailDialog title={title} steps={steps} statusLabel={statusLabel} onClose={onClose}>{children}</ActivityDetailDialog>;
}

/**
 * 助理面板「动态」：等你的事排最上面，其余按天分组。今日概览在「目标」标签底部，这里不再折叠一份。
 * 来源不全时照实说，不把读不到当成没有。
 */
export function AssistantActivity({ items, currentSession, connected, children, onOpen, sources = [], onRefresh, client }: {
  items: FeedItem[]; currentSession?: string; connected: boolean;
  children?: ReactNode; onOpen: (item: FeedItem) => void;
  sources?: ActivitySource[]; onRefresh?: () => void;
  client?: Pick<GatewayClient,"getAgentActivityDetail"> | null;
}) {
  const [, setClock] = useState(() => new Date());
  useEffect(() => startWorkdayClock(setClock, document), []);
  // A newly received snapshot can be newer than the previous clock tick.
  const now = Date.now();
  const coverage = activityCoverage(sources, connected, now);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = items.find(item => item.id === selectedId);
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
  return <section className="assistant-activity" aria-label="助理动态">
    {!connected && <p className="assistant-activity-offline" role="status">
      {lastReadText ? `下面是 ${lastReadText} 读到的动态` : "下面是上次读到的动态"}，连上之后再核对现在的进度。</p>}
    {connected && !coverage.complete && <ActivitySourcesNotice sources={sources} connected={connected} now={now} onRefresh={onRefresh} />}
    {children}
    <div className="assistant-activity-list">
      <ActivityFeed items={items} scope="all" currentSession={currentSession} onOpen={onOpen}
        onInspect={item => { if (item.confirmationId || item.codexActionId || item.dispatchId) onOpen(item); else setSelectedId(item.id); }}
        recordExpansion={{ openIds: openRecords, onToggle: (id, open) => {
          // Ignore delayed native toggle events for records no longer in this source.
          if (!itemsRef.current.some(item => item.id === id)) return;
          setOpenRecords(previous => {
            if (previous.has(id) === open) return previous;
            const next = new Set(previous);
            if (open) next.add(id); else next.delete(id);
            return next;
          });
        } }}
        emptyMessage={!coverage.complete ? "暂未读到这类记录；部分来源待核对，不能据此判断没有事项。" : undefined} />
    </div>
    {selected && <ActivityRecordDialog key={selected.id} item={selected} client={client} onClose={() => setSelectedId(null)} onNavigate={() => onOpen(selected)} />}
  </section>;
}
