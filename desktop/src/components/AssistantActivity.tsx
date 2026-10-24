import { ChevronDown, ListChecks } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { FeedItem } from "../lib/activityFeed";
import { ActivityFeed } from "./ActivityFeed";
import { ActivitySourcesNotice } from "./ActivitySourcesNotice";
import { activityCoverage, type ActivitySource } from "../lib/activitySources";
import { startWorkdayClock } from "../lib/assistantWorkdayReads";
import "./AssistantActivity.css";

/** Show the selected record where the user can see it, including after a long scroll. */
export function AssistantActivityDetail({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    const detail = ref.current, activity = detail?.closest(".assistant-activity");
    ref.current?.scrollIntoView({ block: "nearest" });
    ref.current?.focus({ preventScroll: true });
    return () => {
      // A background update must not steal focus from another input or tab.
      if (document.activeElement !== document.body && !detail?.contains(document.activeElement)) return;
      const target = previous instanceof HTMLElement && previous.isConnected ? previous
        : activity?.isConnected ? activity.querySelector<HTMLElement>('.assistant-activity-filters button[aria-pressed="true"]') : null;
      target?.scrollIntoView({ block: "nearest" }); target?.focus({ preventScroll: true });
    };
  }, []);
  return <section ref={ref} tabIndex={-1} className="assistant-activity-detail" aria-label={title}>
    {children}<button className="assistant-activity-close" onClick={onClose}>收起详情</button>
  </section>;
}

/** Activity stays visible; the workday projection is an optional overview. */
export function AssistantActivity({ items, currentSession, connected, overview, children, onOpen, sources = [], onRefresh }: {
  items: FeedItem[]; currentSession?: string; connected: boolean;
  overview: ReactNode; children?: ReactNode; onOpen: (item: FeedItem) => void;
  sources?: ActivitySource[]; onRefresh?: () => void;
}) {
  const [, setClock] = useState(() => new Date());
  useEffect(() => startWorkdayClock(setClock, document), []);
  // A newly received snapshot can be newer than the previous clock tick.
  const now = Date.now();
  const coverage = activityCoverage(sources, connected, now);
  const [filter, setFilter] = useState<"all" | "waiting">("all");
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
  const listRef = useRef<HTMLDivElement>(null);
  const filtersRef = useRef<HTMLDivElement>(null);
  const allRef = useRef<HTMLButtonElement>(null);
  const previousFilter = useRef(filter);
  useEffect(() => {
    if (previousFilter.current !== filter) {
      const list = listRef.current, panel = list?.closest<HTMLElement>(".assistant-panel-body");
      if (list && panel) panel.scrollTop += list.getBoundingClientRect().top - panel.getBoundingClientRect().top - (filtersRef.current?.offsetHeight ?? 0);
    }
    previousFilter.current = filter;
  }, [filter]);
  const waiting = items.filter(item => item.status === "waiting").length;
  const shown = filter === "waiting" ? items.filter(item => item.status === "waiting") : items;
  const waitingText = coverage.complete ? String(waiting) : waiting ? `${waiting}+` : "待核对";
  return <section className="assistant-activity" aria-label="助理动态">
    <details className="assistant-activity-overview">
      <summary><span><ListChecks size={15} aria-hidden="true" />今日概览
        {waiting > 0 && <small>{waitingText} {connected ? "件待处理" : "条历史待处理"}</small>}</span><ChevronDown size={15} aria-hidden="true" /></summary>
      {overview}
    </details>
    {!connected && <p className="assistant-activity-offline" role="status">连接中断，以下动态为历史记录；恢复连接后再核对当前进度。</p>}
    <ActivitySourcesNotice sources={sources} connected={connected} now={now} onRefresh={onRefresh} />
    <div ref={filtersRef} className="assistant-activity-filters" role="group" aria-label="动态筛选">
      <button ref={allRef} aria-pressed={filter === "all"} onClick={() => setFilter("all")}>全部动态 <span>{items.length}</span></button>
      <button aria-pressed={filter === "waiting"} onClick={() => setFilter("waiting")}>{connected ? "待处理" : "历史待处理"} <span>{waitingText}</span></button>
    </div>
    {children}
    <div ref={listRef} className="assistant-activity-list">
      <ActivityFeed items={shown} scope="all" currentSession={currentSession} onOpen={onOpen}
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
        emptyMessage={!coverage.complete ? "暂未读到这类记录；部分来源待核对，不能据此判断没有事项。"
          : filter === "waiting" ? "已读取的动态中，暂时没有待处理事项。" : undefined} />
      {filter === "waiting" && !shown.length && <button className="assistant-activity-show-all" onClick={() => {
        setFilter("all"); allRef.current?.focus({ preventScroll: true });
      }}>查看全部动态</button>}
    </div>
  </section>;
}
