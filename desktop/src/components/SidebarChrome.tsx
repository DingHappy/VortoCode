// 侧栏上半部的固定件：品牌与搜索框、工作台的主导航、可折叠的分区标题。只负责显示与本地开合。
//
// 搜索框开合是纯本地 UI 态，由 SidebarBrand 自持（它始终挂载）；查询词给项目与会话列表共用，仍由 App 持有。
// 分区的折叠状态也留在 App：切到助理模式再切回来，工作台的分区要保持原样，而那时分区已经卸载过。
import { useState, type ReactNode } from "react";
import { ChevronDown, ListChecks, Package, Search, SquarePen } from "lucide-react";

export function SidebarBrand({ query, onQuery }: { query: string; onQuery: (query: string) => void }) {
  const [searchOpen, setSearchOpen] = useState(false);
  return (
    <>
      <div className="sidebar-brand">
        <strong>VortoCode</strong>
        <button
          className={searchOpen ? "active" : ""}
          aria-label="搜索会话和项目"
          title="搜索会话和项目"
          onClick={() => { setSearchOpen(!searchOpen); onQuery(""); }}
        ><Search size={15} /></button>
      </div>
      {searchOpen && (
        <label className="sidebar-search">
          <Search size={14} />
          <input
            autoFocus
            value={query}
            placeholder="搜索会话和项目"
            onChange={(event) => onQuery(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Escape") { setSearchOpen(false); onQuery(""); } }}
          />
        </label>
      )}
    </>
  );
}

export function WorkNav({ view, connected, onNewChat, onWork, onArtifacts }: {
  view: "chat" | "plugins" | "artifacts" | "management";
  connected: boolean;
  onNewChat: () => void;
  onWork: () => void;
  onArtifacts: () => void;
}) {
  return (
    <nav className="sidebar-nav" aria-label="主导航">
      <button onClick={onNewChat} disabled={!connected}>
        <SquarePen size={16} /><span>新对话</span>
      </button>
      <button className={view === "management" ? "active" : ""} title="派出去的任务、目标和等你确认的事" onClick={onWork}><ListChecks size={16}/><span>工作</span></button>
      <button
        className={view === "artifacts" ? "active" : ""}
        title="Agent 做出来的网页和文档"
        onClick={onArtifacts}
      >
        <Package size={16} /><span>产物</span>
      </button>
    </nav>
  );
}

/** 分区标题：左边是可折叠的名字，右边是这一区的操作按钮。 */
export function SidebarSectionTitle({ label, className, open, onToggle, children }: {
  label: string;
  className: string;
  open: boolean;
  onToggle: () => void;
  children?: ReactNode;
}) {
  return (
    <div className={`sidebar-section-title ${className}`}>
      <button className="sidebar-fold" aria-expanded={open} onClick={onToggle}>
        {label}<ChevronDown size={13} className={open ? "" : "folded"} />
      </button>
      {children}
    </div>
  );
}
