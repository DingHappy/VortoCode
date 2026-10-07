// 输入框下方的「进入项目工作」：搜索并切到一个项目、添加新项目，或回到不绑定目录的通用会话。
import { ChevronDown, Folder, Plus, Search, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { DesktopProjectProfile } from "../types";

const COLLAPSED_COUNT = 3;

type Props = {
  projects: DesktopProjectProfile[];
  activeProjectName: string | null;
  disabled: boolean;
  onPick: (project: DesktopProjectProfile) => void;
  onAddProject: () => void;
  onAddRemote: () => void;
  onLeaveProject: () => void;
};

export function ProjectPicker({ projects, activeProjectName, disabled, onPick, onAddProject, onAddRemote, onLeaveProject }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    const close = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const keyword = query.trim().toLowerCase();
  const usable = projects.filter((project) => !project.missing);
  const matched = keyword
    ? usable.filter((project) => `${project.name} ${project.repoRoot}`.toLowerCase().includes(keyword))
    : usable;
  const shown = keyword || expanded ? matched : matched.slice(0, COLLAPSED_COUNT);
  const choose = (action: () => void) => {
    setOpen(false);
    setQuery("");
    setExpanded(false);
    action();
  };

  return (
    <div className="project-picker" ref={rootRef}>
      <button className="project-picker-trigger" onClick={() => setOpen(!open)} disabled={disabled} aria-expanded={open}>
        {activeProjectName ? <><Folder size={14} />在 {activeProjectName} 中工作</> : "进入项目工作"}
        <ChevronDown size={14} />
      </button>
      {open && (
        <div className="project-picker-menu" role="menu">
          <label className="project-picker-search">
            <Search size={14} />
            <input autoFocus value={query} placeholder="搜索项目" onChange={(event) => setQuery(event.target.value)} />
          </label>
          <div className="project-picker-list">
            {shown.map((project) => (
              <button key={project.id} role="menuitem" title={project.repoRoot} onClick={() => choose(() => onPick(project))}>
                <Folder size={15} /><span>{project.kind === "remote" ? `远端 · ${project.name}` : project.name}</span>
              </button>
            ))}
            {shown.length === 0 && <p>{keyword ? "没有匹配的项目" : "还没有项目"}</p>}
            {!keyword && !expanded && matched.length > COLLAPSED_COUNT && (
              <button className="project-picker-more" onClick={() => setExpanded(true)}>展开显示</button>
            )}
          </div>
          <div className="project-picker-actions">
            <button role="menuitem" onClick={() => choose(onAddProject)}><Plus size={15} /><span>添加新项目</span></button>
            <button role="menuitem" onClick={() => choose(onAddRemote)}><Plus size={15} /><span>连接远端工作区</span></button>
            {activeProjectName && (
              <button role="menuitem" onClick={() => choose(onLeaveProject)}><X size={15} /><span>不使用项目</span></button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
