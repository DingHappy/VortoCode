import { AssistantScheduleCard } from "./AssistantScheduleCard";
import type { ReactNode } from "react";
import { Archive, ArrowLeft, ArrowUpRight, Check, Clock3, Fingerprint, ImagePlus, List, ListChecks, PencilLine, Puzzle, RefreshCw, ShieldCheck, SquarePen, Star, Target, Trash2, X } from "lucide-react";
import { Button, GroupTitle, IconButton, NavItem } from "./ui";
import { useRef, useState } from "react";
import { avatarHue, avatarInitial, avatarSrc } from "../lib/avatar";
import type { AssistantAvatar, AssistantMemory, AssistantProfile, AssistantScheduleItem, AuditEntry, MemoryProposal } from "../types";
export { PhoneChatDialog } from "./PhoneChatDialog";

export type AppMode = "assistant" | "work";

/** 左上角模式切换：助理（个人助理，一个常驻主聊天）/ 工作台（按项目开对话写代码）。全局只有这一处模式切换。 */
export function ModeSwitch({ mode, onChange }: { mode: AppMode; onChange: (mode: AppMode) => void }) {
  return <div className="vc-segmented" role="radiogroup" aria-label="界面模式">
    <button role="radio" aria-checked={mode === "assistant"} onClick={() => onChange("assistant")}>助理</button>
    <button role="radio" aria-checked={mode === "work"} onClick={() => onChange("work")}>工作台</button>
  </div>;
}

export interface SideChat { sid: string; title: string; at?: string; busy?: boolean }

/** 侧栏一级入口（docs/DESIGN_SYSTEM.md 第 2 节）：最多 5 个，「任务」的角标只计等你处理的件数。 */
export type AssistantPage = "tasks" | "schedule" | "goals" | "library" | "customize";

export const ASSISTANT_PAGES: Array<{ id: AssistantPage; label: string; icon: ReactNode; hint: string }> = [
  { id: "tasks", label: "任务", icon: <ListChecks aria-hidden="true" />, hint: "派出去的事、等你处理的事和 Codex 进度" },
  { id: "schedule", label: "定时", icon: <Clock3 aria-hidden="true" />, hint: "早晚报、签到和定时作业" },
  { id: "goals", label: "目标", icon: <Target aria-hidden="true" />, hint: "交给助理盯着的个人目标" },
  { id: "library", label: "资料库", icon: <Archive aria-hidden="true" />, hint: "做出来的网页、文档和文件" },
  { id: "customize", label: "定制", icon: <Puzzle aria-hidden="true" />, hint: "插件、连接的服务、个人技能" },
];

function LiveDot() {
  return <i className="vc-live-dot" aria-label="进行中" />;
}

function matches(title: string, query: string): boolean {
  const q = query.trim().toLowerCase();
  return !q || title.toLowerCase().includes(q);
}

/** 助理模式左侧：新对话、搜索、一级入口，下面是「最近」：主要聊天置顶带星标，手机和本机的聊天在右侧注明来源。 */
export function AssistantSidebar({ mainChat, sideChats, phoneChats = [], offlineChats = [], current, page = null, waiting = 0, waitingUncertain = false,
  search, query = "", onSelect, onPage, onOpenPhoneChat, onOpenOfflineChat, onNewSideChat }: {
  mainChat: { sid: string; preview?: string; busy?: boolean };
  sideChats: SideChat[];
  /** 手机上和助理的聊天：点开只读查看 */
  phoneChats?: Array<{ sid: string; title: string }>;
  onOpenPhoneChat?: (sid: string) => void;
  /** 常驻电脑连不上时在本机聊的（连回常驻电脑后才列出）：点开只读查看，可带回常驻电脑 */
  offlineChats?: Array<{ sid: string; title: string }>;
  onOpenOfflineChat?: (sid: string) => void;
  current: string;
  /** 当前打开的一级入口；null = 在看对话 */
  page?: AssistantPage | null;
  /** 等你处理的件数：「任务」的角标 */
  waiting?: number;
  waitingUncertain?: boolean;
  /** 「搜索 ⌘K」项；搜索词只筛「最近」 */
  search?: ReactNode;
  query?: string;
  onSelect: (sid: string) => void;
  onPage?: (page: AssistantPage) => void;
  onNewSideChat: () => void;
}) {
  const badge = waitingUncertain ? waiting ? `${waiting}+` : "?" : waiting ? String(waiting) : null;
  const sides = sideChats.filter((chat) => matches(chat.title || "新的旁聊", query));
  const phones = phoneChats.filter((chat) => matches(chat.title, query));
  const offline = offlineChats.filter((chat) => matches(chat.title || "本机对话", query));
  return <>
    <nav className="vc-side-nav" aria-label="助理">
      <NavItem icon={<SquarePen aria-hidden="true" />} label="新对话" title="开一个旁聊，不打扰主要聊天" onClick={onNewSideChat} />
      {search}
      <span className="vc-side-gap" aria-hidden="true" />
      {ASSISTANT_PAGES.map((item) => <NavItem key={item.id} icon={item.icon} label={item.label} title={item.hint}
        current={page === item.id} onClick={() => onPage?.(item.id)}
        meta={item.id === "tasks" && badge ? <span className={waiting ? "vc-count" : "vc-count vc-count--unknown"}
          aria-label={waitingUncertain ? `已读取 ${waiting} 件等你处理，部分来源待核对` : `${waiting} 件等你处理`}>{badge}</span> : undefined} />)}
    </nav>
    <div className="vc-side-recent">
      <GroupTitle>最近</GroupTitle>
      {matches("主要聊天", query) && <NavItem className="vc-nav-item--recent" icon={<Star aria-hidden="true" />} label="主要聊天"
        title={mainChat.preview || "主要聊天"} current={!page && current === mainChat.sid} onClick={() => onSelect(mainChat.sid)}
        meta={mainChat.busy ? <LiveDot /> : undefined} />}
      {sides.map((chat) => <NavItem key={chat.sid} className="vc-nav-item--recent" label={chat.title || "新的旁聊"}
        current={!page && current === chat.sid} onClick={() => onSelect(chat.sid)} meta={chat.busy ? <LiveDot /> : undefined} />)}
      {phones.map((chat) => <NavItem key={chat.sid} className="vc-nav-item--recent" label={chat.title.replace(/^📱\s*/, "")}
        title="手机上和助理的聊天（这里只读，在手机上回复）" onClick={() => onOpenPhoneChat?.(chat.sid)} meta="手机" />)}
      {offline.map((chat) => <NavItem key={chat.sid} className="vc-nav-item--recent" label={chat.title || "本机对话"}
        title="常驻电脑连不上时在这台 Mac 上聊的（只读，可带回常驻电脑）" onClick={() => onOpenOfflineChat?.(chat.sid)} meta="本机" />)}
      {!query && sideChats.length === 0 && <p className="vc-side-hint">岔开的话题点「新对话」开个旁聊，不打扰主要聊天。</p>}
    </div>
  </>;
}

/** 助理住在哪：常驻电脑（一直开着）或本机兜底（合上笔记本就停）。 */
export type AssistantHostInfo = { kind: "resident"; name: string; reachable?: boolean } | { kind: "local"; canAddResident: boolean };

/** 助理模式右侧：动态 / 目标 / 审核记录 / 定时 / 身份（SOUL、关于你、记忆）。 */
export type AssistantTab = "feed" | "goals" | "audit" | "schedule" | "identity";

/** 一级入口和右侧面板标签的对应。「资料库」「定制」是独立页面，其余入口打开面板的对应标签。 */
export const PAGE_PANEL: Record<Exclude<AssistantPage, "library" | "customize">, AssistantTab> = {
  tasks: "feed", schedule: "schedule", goals: "goals",
};
export const PANEL_PAGE: Record<AssistantTab, AssistantPage | null> = {
  feed: "tasks", schedule: "schedule", goals: "goals", identity: null, audit: null,
};

const TABS: Array<{ id: AssistantTab; label: string; icon: ReactNode }> = [
  { id: "feed", label: "动态", icon: <List aria-hidden="true" /> },
  { id: "goals", label: "目标", icon: <Target aria-hidden="true" /> },
  { id: "audit", label: "审核记录", icon: <ShieldCheck aria-hidden="true" /> },
  { id: "schedule", label: "定时", icon: <Clock3 aria-hidden="true" /> },
  { id: "identity", label: "身份", icon: <Fingerprint aria-hidden="true" /> },
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
  // 连不上通常是隧道没开或网络断了：服务器上的助理还在干活，只是这台电脑看不到
  const unreachable = host.kind === "resident" && (!connected || host.reachable === false);
  const where = host.kind === "resident"
    ? `住在常驻电脑${host.name && host.name !== "常驻电脑" ? `「${host.name}」` : ""}` : "在本机运行";
  const status = unreachable ? "连不上常驻电脑" : !connected ? "连接中断" : `已连接 · ${where}`;
  const model = !connected ? "" : modelLabel === "未连接模型" ? "还没连接模型" : modelLabel ?? "";
  const badge = waiting || waitingUncertain
    ? waitingUncertain ? `已读取 ${waiting ?? 0} 件待处理，部分来源待核对` : `${waiting} ${connected ? "件等你处理" : "条历史待处理"}` : "";
  return <aside className="assistant-panel vc-panel" aria-label="助理">
    <IconButton className="vc-panel-close" label="收起面板" icon={<X />} onClick={onClose} />
    <div className="vc-panel-card" data-tauri-drag-region="deep">
      <div className="vc-panel-avatar">
        <AssistantAvatarImage name={name} avatar={avatar} size={120} />
        {onEditProfile && <button className="vc-panel-avatar-edit" aria-label="修改助理资料" title="名字、头像与「关于你」" onClick={onEditProfile}><PencilLine aria-hidden="true" /></button>}
      </div>
      <strong className="vc-panel-name">{name}</strong>
      <span className={`vc-panel-status${unreachable || !connected ? " is-down" : ""}`}>{status}</span>
      {model && <small className="vc-panel-model">{model}</small>}
      {unreachable
        ? <>
          <small className="vc-panel-note">它可能还在干活，只是这台电脑看不到</small>
          <Button variant="primary" className="vc-panel-action" onClick={onRetryResident}>重新连接</Button>
        </>
        : host.kind === "local"
          ? <Button className="vc-panel-action" onClick={host.canAddResident ? onAddResident : onRetryResident}>
            {host.canAddResident ? "连接常驻电脑" : "重新连接常驻电脑"}</Button>
          : computer}
    </div>
    <div className="vc-panel-tabs" role="tablist" aria-label="助理面板">
      {TABS.map((item) => <button key={item.id} role="tab" aria-selected={tab === item.id} aria-label={item.label} title={item.label}
        className="vc-panel-tab" onClick={() => onTab(item.id)}>
        {item.icon}
        {item.id === "feed" && badge ? <i className={`vc-panel-tab-dot${waitingUncertain && !waiting ? " is-unknown" : ""}`} role="img" aria-label={badge} /> : null}
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

export function AssistantIdentity({ profile, avatar, onAvatar, memories, proposals = [], onEdit, onDeleteMemory, onDecideProposal }: {
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
  const latest = memories[0]?.created_at?.slice(5, 10).replace("-", ".");
  // 名片卡写名字和「关于你」；SOUL、记忆是两张彩色卡，代表助理本身（设计规范里唯一允许渐变的地方）
  return <section className="assistant-section vc-identity">
    <div className="vc-identity-card">
      <span className="vc-identity-name">{profile.name || "VortoCode"}</span>
      <span className={`vc-identity-about${profile.about_you ? "" : " empty"}`}>{profile.about_you || "关于你：在哪、做什么、习惯怎么被汇报"}</span>
      <button className="vc-identity-edit" onClick={onEdit}><PencilLine aria-hidden="true" />编辑资料</button>
      {onAvatar && <div className="vc-identity-avatar-actions">
        <button onClick={() => fileRef.current?.click()}><ImagePlus aria-hidden="true" />换头像</button>
        {avatar && <button onClick={() => onAvatar(null)}>用名字首字</button>}
        <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" hidden
          onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) onAvatar(file); }} />
      </div>}
    </div>
    <div className="vc-identity-tiles">
      <button className="vc-identity-tile soul" onClick={onEdit}>
        <b>SOUL</b>
        <small>{profile.soul ? "它的性格与说话方式" : "还没写"}</small>
        <span className="vc-identity-tile-preview">{profile.soul || "点这里定它的性格和说话方式"}</span>
        <span className="vc-identity-tile-foot">{profile.soul ? "去改" : "去写"}<ArrowUpRight aria-hidden="true" /></span>
      </button>
      <button className="vc-identity-tile memory" onClick={() => setShowMemories(true)}>
        <b>记忆</b>
        <small>{memories.length ? `记得 ${memories.length} 件事` : "还没有"}</small>
        <span className="vc-identity-tile-preview">{memories[0]?.content || "聊天时说「记住……」，它就会记下来"}</span>
        <span className="vc-identity-tile-foot">{proposals.length ? `${proposals.length} 条待确认` : latest ?? ""}<ArrowUpRight aria-hidden="true" /></span>
      </button>
    </div>
  </section>;
}

/** 空白对话里的上手示例：点一下填进输入框，改一改再发。 */
export function AssistantStarterTips({ tips, onPick, onDismiss }: {
  tips: import("../lib/starterTips").StarterTip[];
  onPick: (tip: import("../lib/starterTips").StarterTip) => void;
  onDismiss: () => void;
}) {
  if (tips.length === 0) return null;
  // 首页输入框下面的一排建议：点了把整句话填进输入框，完整内容放在悬停提示里
  return <div className="vc-starter" role="group" aria-label="可以这样用">
    {tips.map((tip) => <button key={tip.id} className="vc-starter-chip" title={tip.prompt} onClick={() => onPick(tip)}>{tip.label}</button>)}
    <button className="vc-starter-dismiss" onClick={onDismiss}>不再显示</button>
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
