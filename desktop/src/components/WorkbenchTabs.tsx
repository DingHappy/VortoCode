import type { ReactNode } from "react";
import { PanelRightClose, Plus, X } from "lucide-react";
import type { InspectorTab } from "../lib/inspector";

/** 会话侧栏里的一个工具（任务步骤、预览、代码……）。标签栏与工具列表共用这一份定义。 */
export type WorkbenchTool = {
  id: InspectorTab;
  label: string;
  icon: ReactNode;
  /** 数量或进度，如 "3"、"1/3"；为空不显示 */
  badge?: string;
  /** 正在运行（任务步骤在执行中）：标签上一个跳动的小点 */
  live?: boolean;
  /** 有新动静但还没看：标签上一个提示点 */
  attention?: boolean;
  open: () => void;
};

/**
 * 侧栏顶部的标签栏：打开过的工具一字排开，可逐个关闭；「＋」回到工具列表；最右边收起侧栏。
 * 仿浏览器标签，而不是「一个大标题 + 下拉菜单」。
 */
export function WorkbenchTabs({ tools, tabs, current, launcher, onShowLauncher, onCloseTab, onClosePanel }: {
  tools: WorkbenchTool[]; tabs: InspectorTab[]; current: InspectorTab; launcher: boolean;
  onShowLauncher: () => void; onCloseTab: (tab: InspectorTab) => void; onClosePanel: () => void;
}) {
  return <div className="workbench-tabs">
    <div className="workbench-tab-list" role="tablist" aria-label="会话工具">
      {tabs.map((id) => {
        const tool = tools.find((item) => item.id === id);
        if (!tool) return null;
        const active = !launcher && current === id;
        return <div key={id} className={`workbench-tab${active ? " active" : ""}`}>
          <button role="tab" aria-selected={active} className="workbench-tab-main" onClick={tool.open}>
            {tool.icon}<span>{tool.label}</span>
            {tool.live && !active && <i className="workbench-tab-live" aria-label="运行中" />}
            {tool.attention && !active && <i className="workbench-tab-attention" aria-label="有新动静" />}
          </button>
          <button className="workbench-tab-close" aria-label={`关闭${tool.label}`} title={`关闭${tool.label}`} onClick={() => onCloseTab(id)}><X size={12} /></button>
        </div>;
      })}
      <button className={`workbench-tab-add${launcher ? " active" : ""}`} aria-label="打开工具" title="打开工具" onClick={onShowLauncher}><Plus size={15} /></button>
    </div>
    <button className="workbench-panel-close" aria-label="收起侧栏" title="收起侧栏" onClick={onClosePanel}><PanelRightClose size={16} /></button>
  </div>;
}

/** 没有打开任何工具（或点了「＋」）时的工具列表：图标 + 名称 + 数量，点一下作为标签打开。 */
export function WorkbenchLauncher({ tools }: { tools: WorkbenchTool[] }) {
  return <section className="workbench-launcher" aria-label="打开工具">
    {tools.map((tool) => <button key={tool.id} className="workbench-launcher-item" onClick={tool.open}>
      <span className="workbench-launcher-icon">{tool.icon}</span>
      <span className="workbench-launcher-label">{tool.label}</span>
      {tool.badge && <small>{tool.badge}</small>}
    </button>)}
  </section>;
}
