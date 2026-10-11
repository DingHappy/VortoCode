import { CircleCheck, CirclePause, CircleX, LoaderCircle, MessageCircleQuestion } from "lucide-react";
import { feedTime, groupFeed, type FeedItem, type FeedStatus } from "../lib/activityFeed";

const STATUS_TEXT: Record<FeedStatus, string> = {
  waiting: "等你处理", running: "进行中", done: "已完成", failed: "失败", stopped: "已停下",
};

function FeedIcon({ status }: { status: FeedStatus }) {
  if (status === "waiting") return <MessageCircleQuestion size={17} aria-hidden="true" />;
  if (status === "running") return <LoaderCircle size={17} aria-hidden="true" className="session-step-spin" />;
  if (status === "failed") return <CircleX size={17} aria-hidden="true" />;
  if (status === "stopped") return <CirclePause size={17} aria-hidden="true" />;
  return <CircleCheck size={17} aria-hidden="true" />;
}

/**
 * 右侧「任务动态」：对话里交代出去的事，在这里按时间看结果（参考 Muse）。
 * 「等你处理」永远置顶；可只看本对话发起的。
 */
export function ActivityFeed({ items, scope, onScope, currentSession, onOpen, now }: {
  items: FeedItem[];
  scope: "all" | "session";
  onScope: (scope: "all" | "session") => void;
  currentSession?: string;
  onOpen: (item: FeedItem) => void;
  now?: Date;
}) {
  // 后端把发起会话规整成 "sid-<id>"，前端当前会话是裸 id：比较前统一去掉前缀。
  const bare = (session?: string) => (session?.startsWith("sid-") ? session.slice(4) : session) || "";
  const shown = scope === "session" ? items.filter((item) => bare(item.session) && bare(item.session) === bare(currentSession)) : items;
  const groups = groupFeed(shown, now);
  return <section className="activity-feed" aria-label="任务动态">
    <div className="activity-feed-scope" role="radiogroup" aria-label="显示范围">
      <button role="radio" aria-checked={scope === "all"} className={scope === "all" ? "active" : ""} onClick={() => onScope("all")}>全部</button>
      <button role="radio" aria-checked={scope === "session"} className={scope === "session" ? "active" : ""} onClick={() => onScope("session")}>本对话</button>
    </div>
    {groups.length === 0 && <p className="activity-feed-empty">
      {scope === "session" ? "这个对话还没有交代出去的任务。" : "还没有任务。在对话里交代一件事，它会出现在这里，做完了会回报结果。"}
    </p>}
    {groups.map((group) => <section key={group.key} className={`activity-feed-group ${group.key === "waiting" ? "waiting" : ""}`}>
      <h5>{group.label}</h5>
      <ul>{group.items.map((item) => <li key={item.id}>
        <button className={`activity-feed-item ${item.status}`} onClick={() => onOpen(item)}>
          <span className="activity-feed-icon"><FeedIcon status={item.status} /></span>
          <span className="activity-feed-text">
            <strong>{item.title}</strong>
            <span>{item.summary}</span>
            <small><span className="sr-only">{STATUS_TEXT[item.status]} · </span>{feedTime(item)}</small>
          </span>
        </button>
      </li>)}</ul>
    </section>)}
  </section>;
}
