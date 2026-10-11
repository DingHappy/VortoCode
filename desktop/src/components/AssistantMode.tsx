import type { ReactNode } from "react";
import { Laptop, ListChecks, MessageCircle, MessagesSquare, Plus, Server, X } from "lucide-react";

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
export function AssistantSidebar({ mainChat, sideChats, current, onSelect, onNewSideChat }: {
  mainChat: { sid: string; preview?: string; busy?: boolean };
  sideChats: SideChat[];
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
    <div className="assistant-section-title"><span>旁聊</span><button aria-label="新建旁聊" title="新建旁聊" onClick={onNewSideChat}><Plus size={15} /></button></div>
    {sideChats.length === 0 && <p className="assistant-side-empty">岔开的话题可以开一个旁聊，不打扰主要聊天。</p>}
    {sideChats.map((chat) => <button key={chat.sid} className={`assistant-side-chat${current === chat.sid ? " active" : ""}`} onClick={() => onSelect(chat.sid)}>
      <MessagesSquare size={15} /><span>{chat.title || "新的旁聊"}</span>{chat.busy && <i className="workbench-tab-live" aria-label="进行中" />}
    </button>)}
  </nav>;
}

/** 助理住在哪：常驻电脑（一直开着）或本机兜底（合上笔记本就停）。 */
export type AssistantHostInfo = { kind: "resident"; name: string } | { kind: "local"; canAddResident: boolean };

/** 助理模式右侧：助理身份 + 动态（它为你做过的每件事）/ 本轮步骤。 */
export function AssistantPanel({ name, connected, host, onAddResident, onRetryResident, modelLabel, tab, onTab, onClose, waiting, children }: {
  name: string; connected: boolean; modelLabel?: string;
  host: AssistantHostInfo;
  /** 还没登记常驻电脑：去登记 */
  onAddResident: () => void;
  /** 登记过但这次没连上、正用本机兜底：再连一次 */
  onRetryResident: () => void;
  tab: "feed" | "steps"; onTab: (tab: "feed" | "steps") => void; onClose: () => void;
  /** 等你处理的件数：动态图标上的提示 */
  waiting?: number;
  children: ReactNode;
}) {
  return <aside className="assistant-panel" aria-label="助理">
    <button className="assistant-panel-close" aria-label="收起侧栏" title="收起侧栏" onClick={onClose}><X size={16} /></button>
    <div className="assistant-identity">
      <div className="assistant-avatar" aria-hidden="true">{name.slice(0, 1).toUpperCase()}</div>
      <strong>{name}</strong>
      <span className={`assistant-status${connected ? " online" : ""}`}>{connected ? "已连接" : "连接中断"}{connected && modelLabel ? ` · ${modelLabel}` : ""}</span>
      <span className={`assistant-host ${host.kind}`}>
        {host.kind === "resident" ? <Server size={13} aria-hidden="true" /> : <Laptop size={13} aria-hidden="true" />}
        {host.kind === "resident" ? `住在常驻电脑「${host.name}」` : "在本机运行"}
      </span>
      {host.kind === "resident"
        ? <small className="assistant-host-note">合上笔记本它也照常干活。</small>
        : host.canAddResident
          ? <button className="assistant-host-action" onClick={onAddResident}>连接常驻电脑</button>
          : <button className="assistant-host-action" onClick={onRetryResident}>重新连接常驻电脑</button>}
    </div>
    <div className="assistant-tabs" role="tablist" aria-label="助理面板">
      <button role="tab" aria-selected={tab === "feed"} className={tab === "feed" ? "active" : ""} onClick={() => onTab("feed")}>
        <MessagesSquare size={15} aria-hidden="true" />动态{waiting ? <i className="assistant-tab-badge" aria-label={`${waiting} 件等你处理`}>{waiting}</i> : null}</button>
      <button role="tab" aria-selected={tab === "steps"} className={tab === "steps" ? "active" : ""} onClick={() => onTab("steps")}>
        <ListChecks size={15} aria-hidden="true" />步骤</button>
    </div>
    <div className="assistant-panel-body">{children}</div>
  </aside>;
}
