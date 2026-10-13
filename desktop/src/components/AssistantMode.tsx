import type { ReactNode } from "react";
import { ArrowLeft, CalendarClock, Smartphone, Check, Clock3, Fingerprint, HeartPulse, Laptop, List, MessageCircle, MessagesSquare, PencilLine, Plus, Server, ShieldCheck, Trash2, X } from "lucide-react";
import { useState } from "react";
import type { AssistantConnector, AssistantMemory, AssistantProfile, AssistantScheduleItem, AuditEntry, MemoryProposal, PhoneChat } from "../types";
import { MarkdownMessage } from "./MarkdownMessage";

export type AppMode = "assistant" | "work";

/** 左上角模式切换：助理（个人助理，一个常驻主聊天）/ 工作台（按项目开对话写代码）。 */
export function ModeSwitch({ mode, onChange }: { mode: AppMode; onChange: (mode: AppMode) => void }) {
  return <div className="mode-switch" role="radiogroup" aria-label="界面模式">
    <button role="radio" aria-checked={mode === "assistant"} className={mode === "assistant" ? "active" : ""} onClick={() => onChange("assistant")}>助理</button>
    <button role="radio" aria-checked={mode === "work"} className={mode === "work" ? "active" : ""} onClick={() => onChange("work")}>工作台</button>
  </div>;
}

export interface SideChat { sid: string; title: string; at?: string; busy?: boolean }

/** 助理模式左侧：「主要聊天」常驻置顶，岔开的话题放「旁聊」。 */
export function AssistantSidebar({ mainChat, sideChats, phoneChats = [], current, onSelect, onOpenPhoneChat, onNewSideChat }: {
  mainChat: { sid: string; preview?: string; busy?: boolean };
  sideChats: SideChat[];
  /** 手机上和助理的聊天：点开只读查看 */
  phoneChats?: Array<{ sid: string; title: string }>;
  onOpenPhoneChat?: (sid: string) => void;
  current: string;
  onSelect: (sid: string) => void;
  onNewSideChat: () => void;
}) {
  return <nav className="assistant-sidebar" aria-label="助理对话">
    <button className={`assistant-main-chat${current === mainChat.sid ? " active" : ""}`} onClick={() => onSelect(mainChat.sid)}>
      <span className="assistant-main-chat-icon"><MessageCircle size={16} /></span>
      <span className="assistant-main-chat-text"><strong>主要聊天</strong>{mainChat.preview && <small>{mainChat.preview}</small>}</span>
      {mainChat.busy && <i className="workbench-tab-live" aria-label="进行中" />}
    </button>
    {phoneChats.map((chat) => <button key={chat.sid} className="assistant-side-chat phone" onClick={() => onOpenPhoneChat?.(chat.sid)}
      title="手机上和助理的聊天（这里只读，在手机上回复）">
      <Smartphone size={15} /><span>{chat.title.replace(/^📱\s*/, "")} · 手机</span>
    </button>)}
    <div className="assistant-section-title"><span>旁聊</span><button aria-label="新建旁聊" title="新建旁聊" onClick={onNewSideChat}><Plus size={15} /></button></div>
    {sideChats.length === 0 && <p className="assistant-side-empty">岔开的话题可以开一个旁聊，不打扰主要聊天。</p>}
    {sideChats.map((chat) => <button key={chat.sid} className={`assistant-side-chat${current === chat.sid ? " active" : ""}`} onClick={() => onSelect(chat.sid)}>
      <MessagesSquare size={15} /><span>{chat.title || "新的旁聊"}</span>{chat.busy && <i className="workbench-tab-live" aria-label="进行中" />}
    </button>)}
  </nav>;
}

/** 助理住在哪：常驻电脑（一直开着）或本机兜底（合上笔记本就停）。 */
export type AssistantHostInfo = { kind: "resident"; name: string; reachable?: boolean } | { kind: "local"; canAddResident: boolean };

/** 助理模式右侧（参考 Muse）：动态 / 审核记录 / 定时 / 身份（SOUL、关于你、记忆）。 */
export type AssistantTab = "feed" | "audit" | "schedule" | "identity";

const TABS: Array<{ id: AssistantTab; label: string; icon: ReactNode }> = [
  { id: "feed", label: "动态", icon: <List size={16} aria-hidden="true" /> },
  { id: "audit", label: "审核记录", icon: <ShieldCheck size={16} aria-hidden="true" /> },
  { id: "schedule", label: "定时", icon: <Clock3 size={16} aria-hidden="true" /> },
  { id: "identity", label: "身份", icon: <Fingerprint size={16} aria-hidden="true" /> },
];

export function AssistantPanel({ name, connected, host, onAddResident, onRetryResident, onEditProfile, modelLabel, tab, onTab, onClose, waiting, children }: {
  name: string; connected: boolean; modelLabel?: string;
  host: AssistantHostInfo;
  /** 还没登记常驻电脑：去登记 */
  onAddResident: () => void;
  /** 登记过但这次没连上、正用本机兜底：再连一次 */
  onRetryResident: () => void;
  /** 改名字与「关于你」 */
  onEditProfile?: () => void;
  tab: AssistantTab; onTab: (tab: AssistantTab) => void; onClose: () => void;
  /** 等你处理的件数：动态图标上的提示 */
  waiting?: number;
  children: ReactNode;
}) {
  return <aside className="assistant-panel" aria-label="助理">
    <button className="assistant-panel-close" aria-label="收起侧栏" title="收起侧栏" onClick={onClose}><X size={16} /></button>
    <div className="assistant-identity">
      <div className="assistant-avatar">
        <span aria-hidden="true">{name.slice(0, 1).toUpperCase()}</span>
        {onEditProfile && <button aria-label="修改助理资料" title="名字、SOUL 与「关于你」" onClick={onEditProfile}><PencilLine size={12} /></button>}
      </div>
      <strong className="assistant-name">{name}</strong>
      <span className={`assistant-status${connected ? " online" : ""}`}>{connected ? "已连接" : "连接中断"}{connected && modelLabel ? ` · ${modelLabel}` : ""}</span>
      <span className={`assistant-host ${host.kind}`}>
        {host.kind === "resident" ? <Server size={13} aria-hidden="true" /> : <Laptop size={13} aria-hidden="true" />}
        {host.kind === "resident" ? `住在常驻电脑${host.name && host.name !== "常驻电脑" ? `「${host.name}」` : ""}` : "在本机运行"}
      </span>
      {host.kind === "resident"
        ? host.reachable === false
          // 连不上通常是隧道没开或网络断了：服务器上的助理还在干活，只是这台电脑看不到
          ? <button className="assistant-host-action" onClick={onRetryResident}>重新连接常驻电脑</button>
          : <small className="assistant-host-note">合上笔记本它也照常干活。</small>
        : host.canAddResident
          ? <button className="assistant-host-action" onClick={onAddResident}>连接常驻电脑</button>
          : <button className="assistant-host-action" onClick={onRetryResident}>重新连接常驻电脑</button>}
    </div>
    <div className="assistant-tabs icons" role="tablist" aria-label="助理面板">
      {TABS.map((item) => <button key={item.id} role="tab" aria-selected={tab === item.id} aria-label={item.label} title={item.label}
        className={tab === item.id ? "active" : ""} onClick={() => onTab(item.id)}>
        {item.icon}
        {item.id === "feed" && waiting ? <i className="assistant-tab-badge" aria-label={`${waiting} 件等你处理`}>{waiting}</i> : null}
      </button>)}
    </div>
    <div className="assistant-panel-body">{children}</div>
  </aside>;
}

/** 「记忆」页：它记住的关于你的事，可以删。新记住的事从下一个对话起自动带上。 */
export function AssistantMemories({ memories, onDelete }: { memories: AssistantMemory[]; onDelete: (memory: AssistantMemory) => void }) {
  if (memories.length === 0) {
    return <p className="assistant-memory-empty">还没有记住什么。聊天时说「记住……」，或者它觉得重要时会问你要不要记下来。</p>;
  }
  return <ul className="assistant-memories">
    {memories.map((memory) => <li key={memory.id}>
      <span>{memory.content}</span>
      <button aria-label="忘掉这条" title="忘掉这条" onClick={() => onDelete(memory)}><Trash2 size={14} /></button>
    </li>)}
  </ul>;
}

/** 名字与「关于你」：两端（Desktop、手机）共用。 */
export function AssistantProfileDialog({ profile, onSave, onClose }: {
  profile: AssistantProfile;
  onSave: (profile: AssistantProfile) => Promise<void>;
  onClose: () => void;
}) {
  const [name, setName] = useState(profile.name);
  const [about, setAbout] = useState(profile.about_you);
  const [soul, setSoul] = useState(profile.soul);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setBusy(true); setError("");
    try { await onSave({ name: name.trim(), soul: soul.trim(), about_you: about.trim() }); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); setBusy(false); }
  }
  return <div className="modal-backdrop"><section className="settings-modal assistant-profile-dialog" role="dialog" aria-modal="true" aria-label="助理资料">
    <h2>助理资料</h2>
    <label>名字<input autoFocus value={name} maxLength={20} onChange={(event) => setName(event.target.value)} disabled={busy} /></label>
    <label>SOUL<textarea rows={4} maxLength={2000} value={soul} onChange={(event) => setSoul(event.target.value)} disabled={busy}
      placeholder="它是什么性格、怎么说话：比如「简短直接，先给结论；不确定就直说」。" /></label>
    <label>关于你<textarea rows={6} maxLength={2000} value={about} onChange={(event) => setAbout(event.target.value)} disabled={busy}
      placeholder="在哪、做什么、习惯怎么被汇报……助理每次对话都会带上，手机上也一样。" /></label>
    <p>改动从下一个对话开始生效。</p>
    {error && <p role="alert">{error}</p>}
    <div className="settings-actions"><button onClick={onClose} disabled={busy}>取消</button><button onClick={() => void save()} disabled={busy || !name.trim()}>{busy ? "正在保存…" : "保存"}</button></div>
  </section></div>;
}

function ago(value?: string, now: Date = new Date()): string {
  const time = Date.parse(String(value || ""));
  if (!Number.isFinite(time)) return "";
  const minutes = Math.max(0, Math.round((now.getTime() - time) / 60000));
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes}分钟前`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}小时前`;
  return `${Math.round(hours / 24)}天前`;
}

/** 「审核记录」：它请你确认过的事——做了什么、你允许还是拒绝、什么时候（决策审计）。 */
export function AssistantAudit({ entries, now }: { entries: AuditEntry[]; now?: Date }) {
  const decisions = entries.filter((entry) => entry.category === "decision" && entry.operation);
  return <section className="assistant-section">
    <h5>审核记录</h5>
    {decisions.length === 0 && <p className="assistant-memory-empty">还没有需要你确认的事。它要发消息、下发任务、写记忆这类动作前都会先问你，问过的都记在这里。</p>}
    <ul className="assistant-audit">
      {decisions.map((entry) => {
        const [title, ...rest] = String(entry.operation).split("\n").map((line) => line.trim()).filter(Boolean);
        return <li key={entry.id}>
          <span className={`assistant-audit-icon ${entry.decision}`}>{entry.decision === "allowed" ? <Check size={13} /> : <X size={13} />}</span>
          <span className="assistant-audit-text">
            <strong>{title}</strong>
            {rest.length > 0 && <span>{rest.join(" ").slice(0, 120)}</span>}
            <small>{entry.decision === "allowed" ? "已允许" : "已拒绝"}{entry.tainted ? " · 读过外部内容后" : ""}{entry.ts ? ` · ${ago(entry.ts, now)}` : ""}</small>
          </span>
        </li>;
      })}
    </ul>
  </section>;
}

/** 「定时」：它按时间自己会做的事（早晚报、跟进、Heartbeat、定时作业）。 */
export function AssistantSchedule({ items }: { items: AssistantScheduleItem[] }) {
  const icon = (kind: string) => kind === "heartbeat" ? <HeartPulse size={15} /> : kind === "cron" ? <CalendarClock size={15} /> : <Clock3 size={15} />;
  return <section className="assistant-section">
    <h5>定时</h5>
    {items.length === 0 && <p className="assistant-memory-empty">还没有定时的事。比如让它「每天早上给我一份简报」，或者在常驻电脑上打开早晚报。</p>}
    <ul className="assistant-schedule">
      {items.map((item) => <li key={item.id} className={item.enabled ? "" : "disabled"}>
        <span className="assistant-schedule-icon">{icon(item.kind)}</span>
        <span className="assistant-audit-text">
          <strong>{item.title}</strong>
          <small>{item.schedule}{item.enabled ? "" : " · 已停用"}{item.next ? ` · 下次 ${item.next.replace("T", " ").slice(5, 16)}` : ""}</small>
          {item.detail && <span>{item.detail}</span>}
        </span>
      </li>)}
    </ul>
  </section>;
}

/** 「身份」：名字 + SOUL（它的性格）+ 关于你 + 记忆。点记忆卡片看全部、可以删。 */
export function AssistantIdentity({ profile, memories, proposals = [], connectors = [], onEdit, onDeleteMemory, onDecideProposal, onConnector }: {
  profile: AssistantProfile;
  memories: AssistantMemory[];
  /** 聊天里提炼出的候选：你点 ✓ 才记 */
  proposals?: MemoryProposal[];
  onEdit: () => void;
  onDeleteMemory: (memory: AssistantMemory) => void;
  onDecideProposal?: (proposal: MemoryProposal, approve: boolean) => void;
  /** 连接的服务（GitHub 等） */
  connectors?: AssistantConnector[];
  onConnector?: (action: "connect" | "disconnect" | "open" | "copy", connector: AssistantConnector) => void;
}) {
  const [showMemories, setShowMemories] = useState(false);
  if (showMemories) {
    return <section className="assistant-section">
      <h5><button className="assistant-back" aria-label="返回身份" onClick={() => setShowMemories(false)}><ArrowLeft size={14} /></button>记忆</h5>
      {proposals.length > 0 && <>
        <p className="assistant-proposal-title">待确认 · 聊天里听到的，你点 ✓ 才会记住</p>
        <ul className="assistant-memories proposals">
          {proposals.map((proposal) => <li key={proposal.id}>
            <span>{proposal.content}{proposal.tainted && <small className="assistant-proposal-flag">来自手机或外部内容，看仔细</small>}</span>
            <button aria-label="记住" title="记住" onClick={() => onDecideProposal?.(proposal, true)}><Check size={14} /></button>
            <button aria-label="不要" title="不要" onClick={() => onDecideProposal?.(proposal, false)}><X size={14} /></button>
          </li>)}
        </ul>
      </>}
      <AssistantMemories memories={memories} onDelete={onDeleteMemory} />
    </section>;
  }
  const latest = memories[0]?.created_at?.slice(0, 10).replace(/-/g, ".").slice(2);
  return <section className="assistant-section">
    <div className="assistant-profile-card">
      <strong>{profile.name}</strong>
      <button onClick={onEdit}><PencilLine size={13} />编辑</button>
    </div>
    <div className="assistant-cards">
      <button className="assistant-card soul" onClick={onEdit}>
        <b>SOUL</b><small>{profile.soul ? "它的性格与说话方式" : "还没写，点这里定它的性格"}</small>
        <span>{profile.soul.slice(0, 40)}</span>
      </button>
      <button className="assistant-card about" onClick={onEdit}>
        <b>关于你</b><small>{profile.about_you ? "每次对话都带上" : "还没写"}</small>
        <span>{profile.about_you.slice(0, 40)}</span>
      </button>
      <button className="assistant-card memory" onClick={() => setShowMemories(true)}>
        <b>记忆</b><small>{memories.length ? `记得 ${memories.length} 件事` : "还没有"}{proposals.length ? ` · ${proposals.length} 条待确认` : ""}</small>
        <span>{latest ? `最近 ${latest}` : ""}</span>
      </button>
    </div>
    {connectors.length > 0 && <AssistantConnectors connectors={connectors} onAction={onConnector} />}
  </section>;
}

/** 手机上的聊天（只读）：两个进程同时写一段对话会互相覆盖，所以 Desktop 只看不接管。 */
export function PhoneChatDialog({ chat, onClose }: { chat: PhoneChat; onClose: () => void }) {
  return <div className="modal-backdrop" onClick={onClose}><section className="settings-modal phone-chat-dialog" role="dialog" aria-modal="true"
    aria-label={chat.title} onClick={(event) => event.stopPropagation()}>
    <header><h2><Smartphone size={16} />{chat.title.replace(/^📱\s*/, "")} · 手机上的聊天</h2>
      <button aria-label="关闭" onClick={onClose}><X size={16} /></button></header>
    <p className="phone-chat-note">这里只读。要接着聊，在手机上回复——记忆、任务单、动态两边是同一份。</p>
    <div className="phone-chat-messages">
      {chat.transcript.length === 0 && <p className="assistant-memory-empty">还没有聊过。</p>}
      {chat.transcript.map((item, index) => <article key={`${item.rid ?? index}:${item.role}:${index}`} className={`message ${item.role === "user" ? "user" : "assistant"}`}>
        <div className="message-body">{item.role === "user" ? item.text : <MarkdownMessage text={item.text} />}</div>
      </article>)}
    </div>
  </section></div>;
}

const PROVIDER_NAMES: Record<string, string> = { github: "GitHub" };

/** 连接的服务：点「连接」→ 显示验证码 → 你在 github.com/login/device 输入并授权 → 常驻电脑自动连上。 */
export function AssistantConnectors({ connectors, onAction }: {
  connectors: AssistantConnector[];
  onAction?: (action: "connect" | "disconnect" | "open" | "copy", connector: AssistantConnector) => void;
}) {
  return <div className="assistant-connectors">
    <h5>连接的服务</h5>
    <ul>
      {connectors.map((item) => {
        const label = PROVIDER_NAMES[item.provider] ?? item.name;
        return <li key={item.name}>
          <div className="assistant-connector-head">
            <strong>{label}</strong>
            <small>{item.connected ? `已连接${item.read_only ? " · 只读" : ""}`
              : item.authorizing ? "等你在 GitHub 上授权…" : item.expired ? "授权已过期" : "未连接"}</small>
            {item.connected
              ? <button onClick={() => onAction?.("disconnect", item)}>断开</button>
              : !item.authorizing && <button className="primary" onClick={() => onAction?.("connect", item)}>连接</button>}
          </div>
          {item.authorizing && <div className="assistant-connector-code">
            <p>在浏览器打开 GitHub 授权页，输入下面的验证码，点 Authorize：</p>
            <code aria-label="验证码">{item.authorizing.user_code}</code>
            <div>
              <button onClick={() => onAction?.("copy", item)}>复制验证码</button>
              <button className="primary" onClick={() => onAction?.("open", item)}>打开 GitHub</button>
            </div>
          </div>}
          {item.error && !item.connected && <p className="assistant-connector-error" role="alert">{item.error}</p>}
          {item.connected && <p className="assistant-connector-note">只读：能看仓库、Issue、PR 和 CI，不能改任何东西。新开的对话里生效。</p>}
        </li>;
      })}
    </ul>
  </div>;
}
