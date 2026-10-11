import type {InspectorTab} from "../lib/inspector";
export function ManagementNav({current,onSelect}:{current:InspectorTab;onSelect:(tab:InspectorTab)=>void}) {
 return <nav className="inspector-tabs" aria-label="任务中心导航">{([['inbox','看板'],['tasks','任务'],['goals','目标'],['decisions','待处理']] as const).map(([tab,label])=><button key={tab} className={current===tab?'active':''} onClick={()=>onSelect(tab)}>{label}</button>)}</nav>;
}
