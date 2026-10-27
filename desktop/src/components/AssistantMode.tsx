import { AssistantScheduleCard } from "./AssistantScheduleCard";
import type { ReactNode } from "react";
import { ArrowLeft, Smartphone, Check, ChevronRight, Clock3, Fingerprint, ImagePlus, Laptop, List, MessageCircle, MessagesSquare, PencilLine, Plus, RefreshCw, Server, ShieldCheck, Target, Trash2, X } from "lucide-react";
import { useRef, useState } from "react";
import { avatarHue, avatarInitial, avatarSrc } from "../lib/avatar";
import { connectorCredentialView } from "../lib/connectorStatus";
import type { AssistantAvatar, AssistantConnector, AssistantMemory, AssistantPlugin, PluginCheck, AssistantProfile, AssistantScheduleItem, AuditEntry, MemoryProposal } from "../types";
export { PhoneChatDialog } from "./PhoneChatDialog";

export type AppMode = "assistant" | "work";

/** 左上角模式切换：助理（个人助理，一个常驻主聊天）/ 工作台（按项目开对话写代码）。 */
export function ModeSwitch({ mode, resident = true, onChange }: { mode: AppMode; resident?: boolean; onChange: (mode: AppMode) => void }) {
  return <><div className="mode-switch" role="radiogroup" aria-label="界面模式">
    <button role="radio" aria-checked={mode === "assistant"} className={mode === "assistant" ? "active" : ""} onClick={() => onChange("assistant")}>助理</button>
    <button role="radio" aria-checked={mode === "work"} className={mode === "work" ? "active" : ""} onClick={() => onChange("work")}>工作台</button>
  </div>
  <p className="mode-caption">{mode === "assistant"
      ? resident ? "常驻电脑上的个人助理：交代事情、看简报，合上笔记本也在做" : "你的个人助理：交代事情、看简报。连上常驻电脑后，合上笔记本也能接着做"
      : "在这台电脑的项目里写代码：开对话、派任务、看产物"}</p>
  </>;
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

/** 助理模式右侧：动态 / 目标 / 审核记录 / 定时 / 身份（SOUL、关于你、记忆）。 */
export type AssistantTab = "feed" | "goals" | "audit" | "schedule" | "identity";

const TABS: Array<{ id: AssistantTab; label: string; icon: ReactNode }> = [
  { id: "feed", label: "动态", icon: <List size={16} aria-hidden="true" /> },
  { id: "goals", label: "目标", icon: <Target size={16} aria-hidden="true" /> },
  { id: "audit", label: "审核记录", icon: <ShieldCheck size={16} aria-hidden="true" /> },
  { id: "schedule", label: "定时", icon: <Clock3 size={16} aria-hidden="true" /> },
  { id: "identity", label: "身份", icon: <Fingerprint size={16} aria-hidden="true" /> },
];

export function AssistantPanel({ name, avatar, connected, host, onAddResident, onRetryResident, onEditProfile, modelLabel, tab, onTab, onClose, waiting, waitingUncertain = false, children, computer }: {
  name: string; connected: boolean; modelLabel?: string;
  avatar?: AssistantAvatar | null;
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
  waitingUncertain?: boolean;
  children: ReactNode;
  computer?: ReactNode;
}) {
  return <aside className="assistant-panel" aria-label="助理">
    <button className="assistant-panel-close" aria-label="收起侧栏" title="收起侧栏" onClick={onClose}><X size={16} /></button>
    <div className="assistant-identity">
      <div className="assistant-avatar">
        <AssistantAvatarImage name={name} avatar={avatar} />
        {onEditProfile && <button aria-label="修改助理资料" title="名字、SOUL 与「关于你」" onClick={onEditProfile}><PencilLine size={12} /></button>}
      </div>
      <strong className="assistant-name">{name}</strong>
      <span className={`assistant-status${connected ? " online" : ""}`}>{!connected ? "连接中断" : modelLabel === "未连接模型" ? "还没连接模型" : `已连接${modelLabel ? ` · ${modelLabel}` : ""}`}</span>
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
    {computer}
    <div className="assistant-tabs labeled" role="tablist" aria-label="助理面板">
      {TABS.map((item) => <button key={item.id} role="tab" aria-selected={tab === item.id} aria-label={item.label} title={item.label}
        className={tab === item.id ? "active" : ""} onClick={() => onTab(item.id)}>
        {item.icon}<span>{item.label}</span>
        {item.id === "feed" && (waiting || waitingUncertain) ? <i className="assistant-tab-badge"
          aria-label={waitingUncertain ? `已读取 ${waiting ?? 0} 件待处理，部分来源待核对` : `${waiting} ${connected ? "件等你处理" : "条历史待处理"}`}>
          {waitingUncertain ? waiting ? `${waiting}+` : "?" : waiting}</i> : null}
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
export function AssistantSchedule({ items, connected = false, local = false, now, onToggle, onHistory, onRefresh }: {
  items: AssistantScheduleItem[]; connected?: boolean; now?: Date;
  /** 助理在本机运行：本机引擎不开调度，定时的事不会按时自动跑——空状态别再引导去建定时汇报。 */
  local?: boolean;
  onRefresh?: () => Promise<void>;
  onToggle?: (item: AssistantScheduleItem) => Promise<void>;
  onHistory?: (item: AssistantScheduleItem) => Promise<import("../types").AssistantScheduleRun[]>;
}) {
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const readOnly = items.some((item) => item.kind !== "goal" && !(item.kind === "cron" && item.revision));
  return <section className="assistant-section">
    <h5>定时{onRefresh && <button className="assistant-section-refresh" aria-label="刷新定时" title="刷新" disabled={!connected || refreshing} onClick={() => {
      setRefreshing(true); setError("");
      void onRefresh().catch(error => setError(String(error))).finally(() => setRefreshing(false));
    }}><RefreshCw size={13} aria-hidden="true" className={refreshing ? "session-step-spin" : ""} /></button>}</h5>
    {error && <p role="alert">{error}</p>}
    {items.length === 0 && <p className="assistant-memory-empty">{local
      ? "助理现在在本机运行：只在 Desktop 开着时工作，不会按时自动跑定时的事。要每天定时汇报、提醒，需要连接一台一直开着的常驻电脑。"
      : "还没有定时的事。比如让它「每天早上给我一份简报」，或者在常驻电脑上打开早晚报。"}</p>}
    <ul className="assistant-schedule">
      {items.map(item => <AssistantScheduleCard key={item.id} item={item} connected={connected} now={now}
        onToggle={onToggle || (async () => { throw new Error("当前界面不支持管理"); })}
        onHistory={onHistory || (async () => [])} />)}
    </ul>
    {readOnly && <p className="assistant-schedule-note">标「只读」的由助理所在电脑的配置决定，这里只能查看。</p>}
  </section>;
}

/** 「身份」：名字 + SOUL（它的性格）+ 关于你 + 记忆。点记忆卡片看全部、可以删。 */
/** 待确认的记忆；可能更新了旧记忆的，多一个「记住并替换旧的」。 */
export function MemoryProposals({ proposals, onDecide }: {
  proposals: MemoryProposal[];
  onDecide?: (proposal: MemoryProposal, approve: boolean, replace?: string[]) => void;
}) {
  if (!proposals.length) return null;
  return <>
    <p className="assistant-proposal-title">待确认 · 聊天里听到的，你点 ✓ 才会记住</p>
    <ul className="assistant-memories proposals">
      {proposals.map((proposal) => <li key={proposal.id}>
        <span>{proposal.content}{proposal.tainted && <small className="assistant-proposal-flag">来自手机或外部内容，看仔细</small>}
          {proposal.related?.length ? <small className="assistant-proposal-related">可能更新了：{proposal.related.map((item) => item.content).join("；")}</small> : null}</span>
        {proposal.related?.length ? <button aria-label="记住并替换旧的" title="记住，并删掉被它更新的旧记忆"
          onClick={() => onDecide?.(proposal, true, proposal.related?.map((item) => item.id))}><RefreshCw size={14} /></button> : null}
        <button aria-label="记住" title={proposal.related?.length ? "记住（旧的也保留）" : "记住"} onClick={() => onDecide?.(proposal, true)}><Check size={14} /></button>
        <button aria-label="不要" title="不要" onClick={() => onDecide?.(proposal, false)}><X size={14} /></button>
      </li>)}
    </ul>
  </>;
}

export function AssistantIdentity({ profile, avatar, onAvatar, memories, proposals = [], connectors = [], onEdit, onDeleteMemory, onDecideProposal, onConnector, plugins }: {
  profile: AssistantProfile;
  /** 头像：选了文件就换，null 恢复成名字首字 */
  avatar?: AssistantAvatar | null;
  onAvatar?: (file: File | null) => void;
  memories: AssistantMemory[];
  /** 聊天里提炼出的候选：你点 ✓ 才记 */
  proposals?: MemoryProposal[];
  onEdit: () => void;
  onDeleteMemory: (memory: AssistantMemory) => void;
  onDecideProposal?: (proposal: MemoryProposal, approve: boolean, replace?: string[]) => void;
  /** 连接的服务（GitHub 等） */
  connectors?: AssistantConnector[];
  onConnector?: (action: "connect" | "disconnect" | "open" | "copy", connector: AssistantConnector) => void;
  /** 插件区块（日历、Notion…），由上层装好状态后传进来 */
  plugins?: ReactNode;
}) {
  const [showMemories, setShowMemories] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  if (showMemories) {
    return <section className="assistant-section">
      <h5><button className="assistant-back" aria-label="返回身份" onClick={() => setShowMemories(false)}><ArrowLeft size={14} /></button>记忆</h5>
      <MemoryProposals proposals={proposals} onDecide={onDecideProposal} />
      <AssistantMemories memories={memories} onDelete={onDeleteMemory} />
    </section>;
  }
  const latest = memories[0]?.created_at?.slice(0, 10);
  const rows: Array<{ id: string; label: string; status: string; preview: string; filled: boolean; pending?: string; onClick: () => void }> = [
    { id: "soul", label: "SOUL", status: profile.soul ? "它的性格与说话方式" : "还没写", preview: profile.soul || "点这里定它的性格和说话方式", filled: Boolean(profile.soul), onClick: onEdit },
    { id: "about", label: "关于你", status: profile.about_you ? "每次对话都带上" : "还没写", preview: profile.about_you || "在哪、做什么、习惯怎么被汇报", filled: Boolean(profile.about_you), onClick: onEdit },
    { id: "memory", label: "记忆", status: memories.length ? `记得 ${memories.length} 件事` : "还没有",
      preview: memories[0]?.content || "聊天时说「记住……」，它就会记下来", filled: memories.length > 0, pending: proposals.length ? `${proposals.length} 条待确认` : undefined,
      onClick: () => setShowMemories(true) },
  ];
  return <section className="assistant-section">
    <div className="assistant-profile-actions">
      <button onClick={onEdit}><PencilLine size={13} aria-hidden="true" />编辑资料</button>
      {onAvatar && <>
        <button onClick={() => fileRef.current?.click()}><ImagePlus size={13} aria-hidden="true" />换头像</button>
        {avatar && <button onClick={() => onAvatar(null)}>用名字首字</button>}
        <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" hidden
          onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) onAvatar(file); }} />
      </>}
    </div>
    <ul className="assistant-identity-list">
      {rows.map((row) => <li key={row.id}><button className={`assistant-identity-row ${row.id}`} onClick={row.onClick}>
        <span className="assistant-identity-text">
          <span className="assistant-identity-head"><b>{row.label}</b><small>{row.status}</small>
            {row.pending && <small className="assistant-identity-pending">{row.pending}</small>}</span>
          <span className={`assistant-identity-preview${row.filled ? "" : " empty"}`}>{row.preview}</span>
          {row.id === "memory" && latest && <small className="assistant-identity-meta">最近一条记于 {latest}</small>}
        </span>
        <ChevronRight size={15} aria-hidden="true" />
      </button></li>)}
    </ul>
    {connectors.length > 0 && <AssistantConnectors connectors={connectors} onAction={onConnector} />}
    {plugins}
  </section>;
}

const PLUGIN_STATUS: Record<AssistantPlugin["status"], string> = {
  available: "已接上", missing: "未接入", invalid: "凭据文件不可用", revoked: "已在本机撤销",
};

/** 插件：日历、Notion 这类外部数据。凭据在助理所在电脑上放文件，这里只看状态、检查、移除。 */
const PLUGIN_INPUT: Record<string, { label: string; placeholder: string; help: string }> = {
  calendar: { label: "日历订阅链接", placeholder: "https://… 或 webcal://…",
    help: "Google 日历：设置 → 该日历 →「iCal 格式的私密地址」；iCloud：共享日历 → 公开日历 → 复制链接。" },
  caldav: { label: "邮箱:App 专用密码", placeholder: "you@icloud.com:xxxx-xxxx-xxxx-xxxx",
    help: "iCloud：appleid.apple.com → 登录与安全 → App 专用密码。其他支持 CalDAV 的日历写成 https://账号:密码@服务器；末尾加 #日历名 可指定写进哪个日历。只会在你确认后新建日程。" },
  mail: { label: "邮箱:授权码", placeholder: "you@qq.com:授权码",
    help: "在邮箱设置里开 IMAP 并生成授权码（客户端专用密码，不是登录密码）。QQ/163/126/iCloud/Gmail 自动识别服务器，其他写成 imaps://邮箱:授权码@服务器。" },
  notion: { label: "Notion 集成令牌", placeholder: "ntn_…",
    help: "notion.so/profile/integrations 新建内部集成（只勾读取内容），再在要给助理看的页面「连接」里加上它。" },
};

export function AssistantPlugins({ plugins, checks = {}, busy = "", onCheck, onRemove, onSave }: {
  plugins: AssistantPlugin[];
  checks?: Record<string, PluginCheck>;
  /** 正在检查、保存或移除的插件名 */
  busy?: string;
  onCheck?: (plugin: AssistantPlugin) => void;
  onRemove?: (plugin: AssistantPlugin) => void;
  /** 保存凭据；成功返回 true，输入框随之清空收起 */
  onSave?: (plugin: AssistantPlugin, credential: string) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState("");
  const [draft, setDraft] = useState("");
  if (!plugins.length) return null;
  return <div className="assistant-connectors assistant-plugins">
    <h5>插件</h5>
    <ul>
      {plugins.map((item) => {
        const check = checks[item.name];
        const ready = item.status === "available";
        return <li key={item.name}>
          <div className="assistant-connector-head">
            <strong>{item.title}</strong>
            <small>{busy === item.name ? "处理中…" : PLUGIN_STATUS[item.status] ?? item.status}</small>
            {ready && item.checkable && <button disabled={busy === item.name} onClick={() => onCheck?.(item)}>检查</button>}
            {onSave && editing !== item.name && <button className={ready ? "" : "primary"} disabled={busy === item.name}
              onClick={() => { setEditing(item.name); setDraft(""); }}>{ready ? "更换" : "填写"}</button>}
            {item.status !== "missing" && <button disabled={busy === item.name} onClick={() => onRemove?.(item)}>移除凭据</button>}
          </div>
          {editing === item.name && <form className="assistant-plugin-form" onSubmit={(event) => {
            event.preventDefault();
            const value = draft.trim();
            if (!value || !onSave) return;
            void onSave(item, value).then((ok) => { if (ok) { setEditing(""); setDraft(""); } });
          }}>
            <label>{PLUGIN_INPUT[item.name]?.label ?? "凭据"}
              <input type="password" autoComplete="off" spellCheck={false} value={draft} autoFocus
                placeholder={PLUGIN_INPUT[item.name]?.placeholder} onChange={(event) => setDraft(event.target.value)} />
            </label>
            {PLUGIN_INPUT[item.name] && <p className="assistant-connector-note">{PLUGIN_INPUT[item.name].help}</p>}
            <div>
              <button type="button" onClick={() => { setEditing(""); setDraft(""); }}>取消</button>
              <button type="submit" className="primary" disabled={!draft.trim() || busy === item.name}>保存并检查</button>
            </div>
          </form>}
          {check && <p className={check.ok ? "assistant-connector-note" : "assistant-connector-error"} role={check.ok ? undefined : "alert"}>{check.detail}</p>}
          {!ready && editing !== item.name && <p className="assistant-connector-note">点「填写」把凭据存到助理所在的电脑（<code>{item.credential_file}</code>，权限 600），新开对话生效。</p>}
        </li>;
      })}
    </ul>
    <p className="assistant-connector-note">插件都只读，带回来的内容当外部资料处理。凭据只存在助理所在的电脑上，填完不会再显示；移除后立即停用。</p>
  </div>;
}

const PROVIDER_NAMES: Record<string, string> = { github: "GitHub" };

/** 设备授权只保存凭据；实际 MCP 连接由当前会话的插件页观察。 */
export function AssistantConnectors({ connectors, onAction }: {
  connectors: AssistantConnector[];
  onAction?: (action: "connect" | "disconnect" | "open" | "copy", connector: AssistantConnector) => void;
}) {
  return <div className="assistant-connectors">
    <h5>连接的服务</h5>
    <ul>
      {connectors.map((item) => {
        const label = PROVIDER_NAMES[item.provider] ?? item.name;
        const credential = connectorCredentialView(item);
        return <li key={item.name}>
          <div className="assistant-connector-head">
            <strong>{label}</strong>
            <small>{credential.label}</small>
            {credential.canClear && <button onClick={() => onAction?.("disconnect", item)}>{item.authorizing ? "取消授权" : item.local_revocation_supported ? "断开本机连接" : "移除凭据"}</button>}
            {!credential.available && !item.authorizing && <button className="primary" onClick={() => onAction?.("connect", item)}>授权</button>}
          </div>
          {item.authorizing && <div className="assistant-connector-code">
            <p>在浏览器打开 GitHub 授权页，输入下面的验证码，点 Authorize：</p>
            <code aria-label="验证码">{item.authorizing.user_code}</code>
            <div>
              <button onClick={() => onAction?.("copy", item)}>复制验证码</button>
              <button className="primary" onClick={() => onAction?.("open", item)}>打开 GitHub</button>
            </div>
          </div>}
          {(item.error || item.credential_error) && !credential.available && <p className="assistant-connector-error" role="alert">{item.error || item.credential_error}</p>}
          {credential.available && <p className="assistant-connector-note">凭据已保存。到插件页连接当前会话并查看连接状态；工具调用仍受会话权限控制。</p>}
        </li>;
      })}
    </ul>
    <p className="assistant-connector-note">凭据可用不代表服务在线。断开以本地回执为准；GitHub 授权和远端已发请求仍需单独核对。</p>
  </div>;
}

/** 空白对话里的上手示例：点一下填进输入框，改一改再发。 */
export function AssistantStarterTips({ tips, onPick, onDismiss }: {
  tips: import("../lib/starterTips").StarterTip[];
  onPick: (tip: import("../lib/starterTips").StarterTip) => void;
  onDismiss: () => void;
}) {
  if (tips.length === 0) return null;
  return <div className="assistant-starter-tips">
    <div className="assistant-starter-head">
      <span>可以这样用</span>
      <button onClick={onDismiss}>不再显示</button>
    </div>
    <div className="assistant-starter-list">
      {tips.map((tip) => <button key={tip.id} onClick={() => onPick(tip)}>
        <strong>{tip.label}</strong>
        <span>{tip.prompt}</span>
      </button>)}
    </div>
  </div>;
}

/** 助理头像：有图用图，没有就用名字首字（颜色随名字）。 */
export function AssistantAvatarImage({ name, avatar, size }: { name: string; avatar?: AssistantAvatar | null; size?: number }) {
  const src = avatarSrc(avatar);
  const style = size ? { width: size, height: size, fontSize: Math.round(size * 0.42) } : undefined;
  if (src) return <img className="assistant-avatar-image" src={src} alt="" style={style} />;
  const hue = avatarHue(name);
  return <span className="assistant-avatar-initial" aria-hidden="true"
    style={{ ...style, background: `linear-gradient(135deg, hsl(${hue} 62% 60%), hsl(${(hue + 24) % 360} 58% 44%))` }}>
    {avatarInitial(name)}
  </span>;
}
