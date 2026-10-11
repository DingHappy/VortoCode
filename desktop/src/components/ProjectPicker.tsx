// 输入框上方的「进入项目工作」：搜索并切到一个项目、添加新项目，或回到不绑定目录的通用会话。
import { ChevronDown, Folder, Laptop, Plus, Search, Server, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { WorkPlace } from "../lib/residentPlace";
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
  /** 「在哪做」：只有本机和常驻电脑都能做这个项目时才有。 */
  place?: { current: WorkPlace; localAvailable: boolean; residentAvailable: boolean; onChange: (place: WorkPlace) => void };
};

export function ProjectPicker({ projects, activeProjectName, disabled, onPick, onAddProject, onAddRemote, onLeaveProject, place }: Props) {
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
        {activeProjectName ? <><Folder size={14} />工作区域：{activeProjectName}</> : "工作区域：未选择项目"}
        <ChevronDown size={14} />
      </button>
      {place && <span className="work-place" role="radiogroup" aria-label="在哪做">
        <button role="radio" aria-checked={place.current === "local"} className={place.current === "local" ? "active" : ""}
          disabled={disabled || !place.localAvailable} title={place.localAvailable ? "在这台电脑的项目目录里做，适合边看边改" : "本机还没有这个项目"}
          onClick={() => place.current !== "local" && place.onChange("local")}><Laptop size={13} />本机</button>
        <button role="radio" aria-checked={place.current === "resident"} className={place.current === "resident" ? "active" : ""}
          disabled={disabled || !place.residentAvailable} title={place.residentAvailable ? "在常驻电脑上做：合上笔记本也照常进行，做完交付分支" : "常驻电脑上还没有这个项目"}
          onClick={() => place.current !== "resident" && place.onChange("resident")}><Server size={13} />常驻电脑</button>
      </span>}
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
