import { useRef, useState } from "react";
import type { FeedItem } from "../lib/activityFeed";
import type { TeamSummary } from "../types";
import "./WaitingInbox.css";

const TEAM_LINE: Record<string, string> = { review: "交付了，等你验收", waiting: "需要你决定" };

function clock(at: string): string {
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

/** 小组里需要主人的：交付待验收、在等主人回答。 */
export function waitingTeams(teams: TeamSummary[]): TeamSummary[] {
  return teams.filter(team => team.status === "review" || team.status === "waiting");
}

/**
 * 主要聊天顶部的「等你处理」（ADR-016）：要主人处理的事只在这里集中出现一次。
 * 只是入口：按钮打开原来的确认卡、任务单、Codex 动作或小组群聊，在那里决定；唯一的原地动作是小组「验收通过」，
 * 与群聊和主对话卡片里的同一个接口。来源不全时照实说。
 */
export function WaitingInbox({ items, teams, exceptions, uncertain = false, connected, onOpen, onOpenTeam, onAcceptTeam, onShowActivity }: {
  items: FeedItem[];
  teams: TeamSummary[];
  /** 失败、中断、来源失联：另起一行，不和等你的混在一起 */
  exceptions: number;
  uncertain?: boolean;
  connected: boolean;
  onOpen: (item: FeedItem) => void;
  onOpenTeam: (id: string) => void;
  onAcceptTeam: (id: string) => Promise<void>;
  onShowActivity: () => void;
}) {
  const [open, setOpen] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const pending = useRef(false);
  const groups = waitingTeams(teams);
  const total = items.length + groups.length;
  if (total === 0 && exceptions === 0 && !uncertain) return null;
  const accept = async (id: string) => {
    if (pending.current || !connected) return;
    pending.current = true; setBusy(id); setError("");
    try { await onAcceptTeam(id); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "验收没有成功，请在群聊里核对"); }
    finally { pending.current = false; setBusy(""); }
  };
  return <section className="waiting-inbox" aria-label="等你处理">
    {total > 0 && <>
      <button type="button" className="waiting-inbox-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <i aria-hidden="true" />等你处理 · {total}{uncertain ? "+" : ""}
        <span>{open ? "收起" : "展开"}</span>
      </button>
      {open && <ul>
        {groups.map(team => <li key={`team:${team.id}`}>
          <div className="waiting-inbox-text"><strong>任务小组「{team.objective}」{TEAM_LINE[team.status]}</strong>
            {team.status === "waiting" && team.waiting_reason && !team.tainted && <small>{team.waiting_reason}</small>}
            {team.tainted && <small>成员读过外部内容，打开群聊核对原文</small>}</div>
          <div className="task-actions">
            <button type="button" onClick={() => onOpenTeam(team.id)}>{team.status === "waiting" ? "去回复" : "打开群聊"}</button>
            {team.status === "review" && <button type="button" className="primary" disabled={!connected || busy === team.id}
              onClick={() => void accept(team.id)}>{busy === team.id ? "正在验收…" : "验收通过"}</button>}
          </div>
        </li>)}
        {items.map(item => <li key={item.id}>
          <div className="waiting-inbox-text"><strong>{item.title}</strong>
            <small>{[item.sourceLabel, item.summary, clock(item.at)].filter(Boolean).join(" · ")}</small></div>
          <div className="task-actions"><button type="button" className="primary" onClick={() => onOpen(item)}>查看并处理</button></div>
        </li>)}
      </ul>}
    </>}
    {(exceptions > 0 || (uncertain && total === 0)) && <button type="button" className="waiting-inbox-check" onClick={onShowActivity}>
      {exceptions > 0 ? `${exceptions} 项需核对` : "部分来源还没读到，等你处理的可能不止这些"} · 查看动态</button>}
    {error && <p role="alert" className="task-error">{error}</p>}
  </section>;
}
