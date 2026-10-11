import type { ReactNode } from "react";
export function WorkbenchNav({current, label, busy, tabClass, onSelect, children}:{current:string;label:string;busy:boolean;tabClass:(tab:"info")=>string;onSelect:(tab:"info")=>void;children:ReactNode}) {
  return <nav className="inspector-tabs" aria-label="工作台导航">
    <button className={tabClass("info")} onClick={()=>onSelect("info")}>任务步骤{busy && <small>运行中</small>}</button>
    <details className="workbench-more"><summary>更多{current!=="info" ? ` · ${label}` : ""}</summary><div className="workbench-other-tabs" onClick={event=>{
      if((event.target as HTMLElement).closest("button"))event.currentTarget.closest("details")?.removeAttribute("open");
    }}>{children}</div></details>
  </nav>;
}
