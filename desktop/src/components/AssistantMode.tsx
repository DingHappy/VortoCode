import type { ReactNode } from "react";
import { Briefcase, ListChecks, MessageCircle, MessagesSquare, Plus, Sparkles, X } from "lucide-react";

export type AppMode = "assistant" | "work";

/** 左上角模式切换：助理（个人助理，一个常驻主聊天）/ 工作台（按项目开对话写代码）。 */
export function ModeSwitch({ mode, onChange }: { mode: AppMode; onChange: (mode: AppMode) => void }) {
  return <div className="mode-switch" role="radiogroup" aria-label="界面模式">
    <button role="radio" aria-checked={mode === "assistant"} className={mode === "assistant" ? "active" : ""} onClick={() => onChange("assistant")}><Sparkles size={14} />助理</button>
    <button role="radio" aria-checked={mode === "work"} className={mode === "work" ? "active" : ""} onClick={() => onChange("work")}><Briefcase size={14} />工作台</button>
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

/** 助理模式右侧：助理身份 + 动态（它为你做过的每件事）/ 本轮步骤。 */
export function AssistantPanel({ name, connected, modelLabel, tab, onTab, onClose, waiting, children }: {
  name: string; connected: boolean; modelLabel?: string;
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
    </div>
    <div className="assistant-tabs" role="tablist" aria-label="助理面板">
      <button role="tab" aria-selected={tab === "feed"} className={tab === "feed" ? "active" : ""} title="动态" aria-label="动态" onClick={() => onTab("feed")}>
        <MessagesSquare size={16} />{waiting ? <i className="assistant-tab-badge">{waiting}</i> : null}</button>
      <button role="tab" aria-selected={tab === "steps"} className={tab === "steps" ? "active" : ""} title="本轮步骤" aria-label="本轮步骤" onClick={() => onTab("steps")}><ListChecks size={16} /></button>
    </div>
    <div className="assistant-panel-body">{children}</div>
  </aside>;
}
