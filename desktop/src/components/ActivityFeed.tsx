import { ChevronDown, ChevronRight, CircleCheck, CirclePause, CircleX, FileCode2, Globe, LoaderCircle, MessageCircleQuestion, SquareTerminal } from "lucide-react";
import { feedTime, groupFeed, type FeedItem, type FeedKind, type FeedStatus } from "../lib/activityFeed";

const STATUS_TEXT: Record<FeedStatus, string> = {
  waiting: "等你处理", running: "进行中", done: "执行结束", failed: "失败", stopped: "已停下",
};

export interface FeedRecordExpansion {
  openIds: ReadonlySet<string>;
  onToggle: (id: string, open: boolean) => void;
}

function FeedEntry({ item, onOpen, now, recordExpansion }: { item: FeedItem; onOpen: (item: FeedItem) => void; now: Date; recordExpansion?: FeedRecordExpansion }) {
  const navigable = Boolean(item.dispatchId || item.taskId || item.session || item.codexActionId || item.confirmationId);
  const validTime = Number.isFinite(Date.parse(item.at));
  const row = <>
    <span className="activity-feed-icon"><FeedIcon status={item.status} kind={item.kind} /></span>
    <span className="activity-feed-text">
      <strong>{item.title}</strong>
      <span>{item.summary}</span>
      <small className="activity-feed-meta">
        <span className="activity-feed-status">{item.statusLabel || STATUS_TEXT[item.status]}</span>
        {item.sourceLabel && <span>{item.sourceLabel}</span>}
        {validTime && <time dateTime={item.at} title={new Date(item.at).toLocaleString("zh-CN")}>{feedTime(item, now)}</time>}
      </small>
    </span>
    {navigable ? <ChevronRight className="activity-feed-chevron" size={14} aria-hidden="true" />
      : <ChevronDown className="activity-feed-chevron" size={14} aria-hidden="true" />}
  </>;
  return navigable
    ? <button className={`activity-feed-item ${item.status}`} onClick={() => onOpen(item)}>{row}</button>
    : <details className={`activity-feed-record ${item.status}`} open={recordExpansion ? recordExpansion.openIds.has(item.id) : undefined}
      onToggle={recordExpansion ? event => recordExpansion.onToggle(item.id, event.currentTarget.open) : undefined}>
      <summary className={`activity-feed-item ${item.status}`}>{row}<span className="sr-only">展开完整记录</span></summary>
      <p className="activity-feed-detail">{item.detail || item.summary || "暂无补充内容。"}</p>
    </details>;
}

function FeedIcon({ status, kind }: { status: FeedStatus; kind?: FeedKind }) {
  if (status === "waiting") return <MessageCircleQuestion size={17} aria-hidden="true" />;
  if (status === "running") return <LoaderCircle size={17} aria-hidden="true" className="session-step-spin" />;
  if (status === "failed") return <CircleX size={17} aria-hidden="true" />;
  if (status === "stopped") return <CirclePause size={17} aria-hidden="true" />;
  // 完成态按做的事选图标：联网查资料 / 跑命令 / 改代码；普通对答与后台任务用对勾
  if (kind === "web") return <Globe size={17} aria-hidden="true" />;
  if (kind === "command") return <SquareTerminal size={17} aria-hidden="true" />;
  if (kind === "code") return <FileCode2 size={17} aria-hidden="true" />;
  return <CircleCheck size={17} aria-hidden="true" />;
}

/**
 * 右侧「任务动态」：对话里交代出去的事，在这里按时间看结果（参考 Muse）。
 * 「等你处理」永远置顶；可只看本对话发起的。
 */
export function ActivityFeed({ items, scope, onScope, currentSession, onOpen, now = new Date(), emptyMessage, recordExpansion }: {
  items: FeedItem[];
  scope: "all" | "session";
  onScope?: (scope: "all" | "session") => void;
  currentSession?: string;
  onOpen: (item: FeedItem) => void;
  now?: Date;
  emptyMessage?: string;
  recordExpansion?: FeedRecordExpansion;
}) {
  // 后端把发起会话规整成 "sid-<id>"，前端当前会话是裸 id：比较前统一去掉前缀。
  const bare = (session?: string) => (session?.startsWith("sid-") ? session.slice(4) : session) || "";
  const shown = scope === "session" ? items.filter((item) => bare(item.session) && bare(item.session) === bare(currentSession)) : items;
  const groups = groupFeed(shown, now);
  return <section className="activity-feed" aria-label="任务动态">
    {onScope && <div className="activity-feed-scope" role="radiogroup" aria-label="显示范围">
      <button role="radio" aria-checked={scope === "all"} className={scope === "all" ? "active" : ""} onClick={() => onScope("all")}>全部</button>
      <button role="radio" aria-checked={scope === "session"} className={scope === "session" ? "active" : ""} onClick={() => onScope("session")}>本对话</button>
    </div>}
    {groups.length === 0 && <p className="activity-feed-empty" role="status">
      {emptyMessage ?? (scope === "session" ? "这个对话还没有交代出去的任务。" : "还没有任务。在对话里交代一件事，它会出现在这里，做完了会回报结果。")}
    </p>}
    {groups.map((group) => <section key={group.key} className={`activity-feed-group ${group.key === "waiting" ? "waiting" : ""}`}>
      <h5>{group.label}</h5>
      <ul>{group.items.map((item) => <li key={item.id}>
        <FeedEntry item={item} onOpen={onOpen} now={now} recordExpansion={recordExpansion} />
      </li>)}</ul>
    </section>)}
  </section>;
}
