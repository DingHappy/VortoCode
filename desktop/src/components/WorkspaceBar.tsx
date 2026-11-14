import type { RefObject } from "react";
import { PanelLeftClose, PanelLeftOpen, Search } from "lucide-react";
import { RoleAvatar } from "./RoleAvatar";
import { IconButton } from "./ui";
import "./WorkspaceBar.css";

/** Shared navigation chrome; buttons use existing sidebar and search state. */
export function WorkspaceBar({ collapsed, disabled = false, toggleRef, onToggle, onSearch }: {
  collapsed: boolean; disabled?: boolean; toggleRef?: RefObject<HTMLButtonElement | null>; onToggle: () => void; onSearch: () => void;
}) {
  return <header inert={disabled} className="vc-workspace-bar" data-tauri-drag-region="deep">
    <div className="vc-workspace-brand"><RoleAvatar character="general" size="small" /><strong>VortoOne</strong>
      <IconButton label={collapsed ? "展开侧栏" : "收起侧栏"} icon={collapsed ? <PanelLeftOpen aria-hidden="true" /> : <PanelLeftClose aria-hidden="true" />}
        buttonRef={toggleRef} aria-expanded={!collapsed} aria-controls="conversation-sidebar" onClick={onToggle} /></div>
    <button className="vc-workspace-search" onClick={onSearch}><Search aria-hidden="true" /><span>搜索聊天与频道</span><kbd>⌘ K</kbd></button>
    <div className="vc-workspace-bar-end" />
  </header>;
}
