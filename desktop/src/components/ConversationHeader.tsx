// 对话区标题栏：会话名、在哪台电脑上做（登记了常驻电脑时）、右侧面板开关或任务胶囊、停止按钮。只负责显示。
import type { ReactNode } from "react";
import { Laptop, PanelRight, Server } from "lucide-react";

export function ConversationHeader({ title, place, panelOpen, waiting, plan, busy, taskPill, status, onTogglePanel, onStop }: {
  title: string;
  /** 登记了常驻电脑、又在工作台项目里时才显示 */
  place: "resident" | "local" | null;
  panelOpen: boolean;
  /** 等你处理的件数 */
  waiting: number;
  plan: { done: number; total: number };
  busy: boolean;
  /** 助理模式用任务胶囊代替右侧面板开关（docs/DESIGN_SYSTEM.md 第 4 节） */
  taskPill?: ReactNode;
  /** 本地引擎没连上时的连接状态；平时不显示 */
  status?: ReactNode;
  onTogglePanel: () => void;
  onStop: () => void;
}) {
  return (
    // 窗口没有标题栏：顶栏的空白处用来拖动窗口
    <div className="conversation-header" data-tauri-drag-region="deep">
      <div>
        <strong>{title}</strong>
        {place && (
          <span className={`conversation-place ${place}`}>
            {place === "resident" ? <><Server size={12} aria-hidden="true" />常驻电脑</> : <><Laptop size={12} aria-hidden="true" />本机</>}
          </span>)}
      </div>
      <div className="conversation-actions">
        {status}
        {taskPill ?? <button
          className={`context-toggle workbench-toggle icon-only ${panelOpen ? "active" : ""}`}
          aria-expanded={panelOpen}
          aria-label="任务动态"
          title={waiting ? `任务动态 · ${waiting} 件等你处理` : plan.total ? `任务动态 · 当前步骤 ${plan.done}/${plan.total}` : "任务动态"}
          onClick={onTogglePanel}
        >
          <PanelRight size={16} />
          {(busy || waiting > 0) && <i className={`workbench-toggle-dot${!busy ? " waiting" : ""}`} aria-hidden="true" />}
        </button>}
        {busy && <button className="stop-button" onClick={onStop}>停止</button>}
      </div>
    </div>
  );
}
