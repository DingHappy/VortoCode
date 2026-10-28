// 侧栏上半部的固定件：品牌与搜索框、工作台的主导航、可折叠的分区标题。只负责显示与本地开合。
//
// 搜索框开合是纯本地 UI 态，由 SidebarBrand 自持（它始终挂载）；查询词给项目与会话列表共用，仍由 App 持有。
// 分区的折叠状态也留在 App：切到助理模式再切回来，工作台的分区要保持原样，而那时分区已经卸载过。
import { useState, type ReactNode } from "react";
import { ChevronDown, ListChecks, Package, Search, SquarePen } from "lucide-react";
import { IconButton, NavItem } from "./ui";

export function SidebarBrand({ query, onQuery }: { query: string; onQuery: (query: string) => void }) {
  const [searchOpen, setSearchOpen] = useState(false);
  return (
    <>
      <div className="vc-side-brand">
        <strong>VortoCode</strong>
        <IconButton
          label="搜索会话和项目"
          icon={<Search />}
          aria-pressed={searchOpen}
          onClick={() => { setSearchOpen(!searchOpen); onQuery(""); }}
        />
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
    <nav className="vc-side-nav" aria-label="主导航">
      <NavItem icon={<SquarePen />} label="新对话" onClick={onNewChat} disabled={!connected} />
      <NavItem icon={<ListChecks />} label="工作" title="派出去的任务、目标和等你确认的事" current={view === "management"} onClick={onWork} />
      <NavItem icon={<Package />} label="产物" title="Agent 做出来的网页和文档" current={view === "artifacts"} onClick={onArtifacts} />
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
