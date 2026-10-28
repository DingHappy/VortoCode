// 侧栏上半部的固定件：品牌与收起按钮、搜索项、工作台的主导航、可折叠的分区标题。只负责显示。
//
// 搜索框开合由 App 持有：⌘K 在侧栏收起时也要能打开它。查询词给项目与会话列表共用。
// 分区的折叠状态也留在 App：切到助理模式再切回来，工作台的分区要保持原样，而那时分区已经卸载过。
import type { ReactNode } from "react";
import { ChevronDown, Library, ListChecks, PanelLeft, Puzzle, Search, SquarePen, Target } from "lucide-react";
import { IconButton, NavItem } from "./ui";

export function SidebarBrand({ onCollapse }: { onCollapse: () => void }) {
  return (
    <div className="vc-side-brand" data-tauri-drag-region="deep">
      <span className="vc-side-logo" aria-hidden="true">V</span>
      <strong>VortoCode</strong>
      <IconButton label="收起侧栏" icon={<PanelLeft />} onClick={onCollapse} />
    </div>
  );
}

/** 「搜索 ⌘K」：点开后就地出现输入框，Esc 收起。 */
export function SidebarSearch({ open, query, placeholder, onOpenChange, onQuery }: {
  open: boolean;
  query: string;
  placeholder: string;
  onOpenChange: (open: boolean) => void;
  onQuery: (query: string) => void;
}) {
  if (!open) {
    return <NavItem icon={<Search />} label="搜索" meta="⌘K" onClick={() => onOpenChange(true)} />;
  }
  return (
    <label className="vc-side-search">
      <Search aria-hidden="true" />
      <input
        autoFocus
        value={query}
        aria-label={placeholder}
        placeholder={placeholder}
        onChange={(event) => onQuery(event.target.value)}
        onBlur={() => { if (!query) onOpenChange(false); }}
        onKeyDown={(event) => { if (event.key === "Escape") { onQuery(""); onOpenChange(false); } }}
      />
    </label>
  );
}

/** 工作台的一级入口（docs/DESIGN_SYSTEM.md 第 2 节；画布 E11）：任务、目标、资料库、扩展。 */
export type WorkPage = "tasks" | "goals" | "library" | "extensions";

const WORK_PAGES: Array<{ id: WorkPage; label: string; icon: ReactNode; hint: string }> = [
  { id: "tasks", label: "任务", icon: <ListChecks />, hint: "派出去的活、会话和等你确认的事" },
  { id: "goals", label: "目标", icon: <Target />, hint: "项目目标和完成标准" },
  { id: "library", label: "资料库", icon: <Library />, hint: "Agent 做出来的网页和文档" },
  { id: "extensions", label: "扩展", icon: <Puzzle />, hint: "MCP 工具、项目技能、Hook 和规则" },
];

export function WorkNav({ page, connected, search, waiting = 0, onNewChat, onPage }: {
  /** 当前打开的一级页面；在对话里时为 null */
  page: WorkPage | null;
  connected: boolean;
  search?: ReactNode;
  /** 「任务」上的角标：等你处理的件数 */
  waiting?: number;
  onNewChat: () => void;
  onPage: (page: WorkPage) => void;
}) {
  return (
    <nav className="vc-side-nav" aria-label="主导航">
      <NavItem icon={<SquarePen />} label="新对话" onClick={onNewChat} disabled={!connected} />
      {search}
      <span className="vc-side-gap" aria-hidden="true" />
      {WORK_PAGES.map((item) => <NavItem key={item.id} icon={item.icon} label={item.label} title={item.hint}
        current={page === item.id} onClick={() => onPage(item.id)}
        meta={item.id === "tasks" && waiting > 0 ? <span className="vc-count" aria-label={`${waiting} 件等你处理`}>{waiting}</span> : undefined} />)}
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
