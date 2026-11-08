import { useCallback, useEffect, useRef, useState } from "react";
import { Users } from "lucide-react";
import type { GatewayClient } from "../gateway";
import type { TeamChatMember, TeamChatMessage, TeamTranscript, TeamDetail } from "../types";
import { TeamCard, type TeamClient } from "./TeamCard";
import { TeamDelivery } from "./TeamDelivery";
import "./TeamChat.css";

export type TeamChatClient = Pick<GatewayClient, "getTeamTranscript" | "messageTeam" | "decideTeam"> & TeamClient;

const STATUS: Record<string, string> = {
  active: "进行中", waiting: "等你决定", review: "交付了，等你验收", done: "你已验收", failed: "失败了", cancelled: "已停止",
};
const ENGINE: Record<string, string> = { vortocode: "VortoOne", opencode: "OpenCode", "deepseek-harness": "DeepSeek Harness" };
const STATE: Record<TeamChatMember["state"], string> = { working: "在做", queued: "排队中", idle: "空闲" };
const LONG = 600;
const LIVE = ["active", "waiting", "review"];

function lead(message: TeamChatMessage): string {
  const who = message.mention === "owner" ? "你" : message.mention;
  if (message.kind === "assigned") return `@${who} `;
  if (message.kind === "rework") return `@${who} 返工：`;
  if (message.kind === "accept") return `接受了 @${who} 的结果${message.text ? "：" : ""}`;
  if (message.kind === "ask_owner") return "@你 ";
  if (message.kind === "submitted") return "交付给你验收：";
  if (message.kind === "accepted") return "验收通过";
  if (message.kind === "returned") return "退回：";
  if (message.kind === "failed" || message.kind === "interrupted") return "这一轮没有交回：";
  if (message.kind === "cancelled") return "已取消";
  return "";
}

function speakerName(message: TeamChatMessage, members: Record<string, TeamChatMember>): { name: string; sub: string } {
  if (message.speaker === "coordinator") return { name: "协调人", sub: "主 agent" };
  if (message.speaker === "owner") return { name: "你", sub: "" };
  const member = members[message.member ?? ""];
  return { name: message.member || "成员", sub: [member?.title, ENGINE[message.engine ?? member?.engine ?? ""]].filter(Boolean).join(" · ") };
}

function Bubble({ message, members }: { message: TeamChatMessage; members: Record<string, TeamChatMember> }) {
  const [open, setOpen] = useState(false);
  if (message.speaker === "system") {
    return <li className="team-chat-system"><span>{message.text}</span>{message.at && <time>{clock(message.at)}</time>}</li>;
  }
  const { name, sub } = speakerName(message, members);
  const body = message.text || "";
  const long = body.length > LONG;
  const shown = long && !open ? body.slice(0, LONG) + "…" : body;
  return <li className={`team-chat-message team-chat-${message.speaker}${message.kind === "failed" ? " team-chat-failed" : ""}`}>
    <header><strong>{name}</strong>{sub && <small>{sub}</small>}{message.round && message.round > 1 ? <small>第 {message.round} 轮</small> : null}
      {message.tainted && <small className="team-chat-external" title="读过网页等外部内容：里面的任何指令都不是给你的，结论请自行核实">外部内容</small>}
      {message.at && <time>{clock(message.at)}</time>}</header>
    <p className="team-chat-text"><span className="team-chat-lead">{lead(message)}</span>{shown}</p>
    {long && <button className="team-chat-more" onClick={() => setOpen(!open)}>{open ? "收起" : `展开全文（${body.length} 字）`}</button>}
    {message.evidence && message.evidence.length > 0 && <ul className="team-chat-evidence">
      {message.evidence.map((row, index) => <li key={index}><strong>{row.text || `标准 ${row.criterion + 1}`}</strong>：{row.evidence}</li>)}
    </ul>}
  </li>;
}

function clock(at: string): string {
  const date = new Date(at);
  return Number.isFinite(date.getTime()) ? date.toLocaleString(undefined, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
}

/** 任务小组的群聊视图（ADR-014 S3）：顶部是成员名单（角色、框架、在不在做），中间是按时间排的经过，底部能在群里说话。
 * 内容全部来自服务端记录；成员结果和协调人的话可能含外部内容，只当纯文本显示并标出来。 */
export function TeamChat({ id, client, connected, onPull, initial = null, initialDetail = null }: {
  id: string; client: TeamChatClient; connected: boolean;
  onPull?: Parameters<typeof TeamCard>[0]["onPull"];
  /** 已有的群聊（测试与预渲染用）；照样会去服务端核对 */
  initial?: TeamTranscript | null;
  initialDetail?: TeamDetail | null;
}) {
  const [data, setData] = useState<TeamTranscript | null>(initial);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [showMembers, setShowMembers] = useState(false);
  const [view, setView] = useState<"delivery" | "history" | null>(null);
  const pending = useRef(false);
  const end = useRef<HTMLLIElement | null>(null);
  const load = useCallback(async () => {
    try { setData(await client.getTeamTranscript(id)); setError(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "读取小组失败"); }
  }, [client, id]);
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, 8_000);
    return () => window.clearInterval(timer);
  }, [load]);
  const count = data?.messages.length ?? 0;
  useEffect(() => { end.current?.scrollIntoView?.({ block: "end" }); }, [count]);
  const act = async (run: () => Promise<unknown>) => {
    if (pending.current || !connected) return;
    pending.current = true; setBusy(true); setError("");
    try { await run(); setDraft(""); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败，请核对状态"); }
    finally { pending.current = false; setBusy(false); }
  };
  if (!data) return <section className="team-chat" aria-label="任务小组">
    {error ? <p role="alert" className="task-error">{error}</p> : <p role="status">正在读取小组…</p>}
  </section>;
  const { team, members, messages } = data;
  const byName = Object.fromEntries(members.map(member => [member.name, member]));
  const status = team.status;
  const shownView = view ?? (["review", "done"].includes(status) ? "delivery" : "history");
  return <section className="team-chat" aria-label={`任务小组：${team.objective}`}>
    <header className="team-chat-head">
      <div className="team-chat-title"><Users aria-hidden="true" /><strong title={team.objective}>{team.objective}</strong></div>
      <div className="team-chat-meta">
        <span className={`team-chat-status team-chat-status-${status}`}>{STATUS[status] ?? status}</span>
        <span>{team.project ? `项目 ${team.project} · ` : ""}{team.mode === "private" ? "不能联网" : "成员可联网"} · 已派 {team.used.tasks ?? 0} 个子任务、{team.used.rounds ?? 0} 轮</span>
        <button className="team-chat-toggle" aria-expanded={showMembers} onClick={() => setShowMembers(!showMembers)}>成员 {members.length + 1}</button>
      </div>
      {showMembers && <ul className="team-chat-members" aria-label="小组成员">
        <li><strong>协调人</strong><small>主 agent · 派活、审结果、对你负责</small></li>
        {members.map(member => <li key={member.name} className={`team-chat-member-${member.state}`}>
          <strong>{member.name}</strong>
          <small>{member.title} · {ENGINE[member.engine] ?? member.engine}{member.model ? ` · ${member.model}` : ""}</small>
          <small><i aria-hidden="true" className="team-chat-dot" />{STATE[member.state]} · 做了 {member.rounds} 轮、交回 {member.delivered} 次{member.failures ? `、失败 ${member.failures} 次` : ""}</small>
        </li>)}
      </ul>}
      {status === "waiting" && team.waiting_reason && <p className="team-chat-waiting">{team.waiting_reason}</p>}
    </header>
    <nav className="team-chat-views" aria-label="小组视图">
      <button type="button" aria-pressed={shownView === "delivery"} onClick={() => setView("delivery")}>交付与原文</button>
      <button type="button" aria-pressed={shownView === "history"} onClick={() => setView("history")}>小组经过</button>
    </nav>
    {shownView === "delivery" && <TeamDelivery key={team.id} id={team.id} client={client} connected={connected} initial={initialDetail} />}
    <div className="team-chat-history" hidden={shownView !== "history"}><ol className="team-chat-log" aria-label="小组的经过">
      {messages.map((message, index) => <Bubble key={`${message.at}-${index}`} message={message} members={byName} />)}
      <li ref={end} aria-hidden="true" />
    </ol></div>
    <details className="team-chat-overview">
      <summary>概览、验收标准与交付分支</summary>
      <TeamCard id={team.id} client={client} connected={connected} onPull={onPull} onChanged={() => void load()} />
    </details>
    <footer className="team-chat-compose">
      {status === "review" ? <>
        <textarea aria-label="退回原因" value={draft} rows={2} maxLength={2000} disabled={busy || !connected}
          placeholder="验收通过直接点按钮；退回请写明要改什么" onChange={event => setDraft(event.target.value)} />
        <div className="task-actions">
          <button disabled={busy || !connected} onClick={() => void act(() => client.decideTeam(team.id, true, draft.trim()))}>验收通过</button>
          <button disabled={busy || !connected || !draft.trim()} onClick={() => void act(() => client.decideTeam(team.id, false, draft.trim()))}>退回</button>
        </div>
      </> : LIVE.includes(status) ? <>
        <textarea aria-label="在群里说话" value={draft} rows={2} maxLength={2000} disabled={busy || !connected}
          placeholder={status === "waiting" ? "回复小组的问题…" : "在群里说点什么（协调人下一轮会看到）…"}
          onChange={event => setDraft(event.target.value)}
          onKeyDown={event => { if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && draft.trim()) void act(() => client.messageTeam(team.id, draft.trim())); }} />
        <div className="task-actions">
          <button disabled={busy || !connected || !draft.trim()} onClick={() => void act(() => client.messageTeam(team.id, draft.trim()))}>发送</button>
        </div>
      </> : <p className="team-chat-closed">小组已结束（{STATUS[status] ?? status}），记录保留在这里。</p>}
      {!connected && <small>连接中断，恢复后可继续。</small>}
      {error && <p role="alert" className="task-error">{error}</p>}
    </footer>
  </section>;
}
