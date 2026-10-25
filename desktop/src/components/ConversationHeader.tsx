// 对话区标题栏：会话名、在哪台电脑上做（登记了常驻电脑时）、右侧面板开关与停止按钮。只负责显示。
import { Laptop, PanelRight, Server } from "lucide-react";

export function ConversationHeader({ title, place, panelOpen, waiting, plan, busy, onTogglePanel, onStop }: {
  title: string;
  /** 登记了常驻电脑、又在工作台项目里时才显示 */
  place: "resident" | "local" | null;
  panelOpen: boolean;
  /** 等你处理的件数 */
  waiting: number;
  plan: { done: number; total: number };
  busy: boolean;
  onTogglePanel: () => void;
  onStop: () => void;
}) {
  return (
    <div className="conversation-header">
      <div>
        <strong>{title}</strong>
        {place && (
          <span className={`conversation-place ${place}`}>
            {place === "resident" ? <><Server size={12} aria-hidden="true" />常驻电脑</> : <><Laptop size={12} aria-hidden="true" />本机</>}
          </span>)}
      </div>
      <div className="conversation-actions">
        <button
          className={`context-toggle workbench-toggle icon-only ${panelOpen ? "active" : ""}`}
          aria-expanded={panelOpen}
          aria-label="任务动态"
          title={waiting ? `任务动态 · ${waiting} 件等你处理` : plan.total ? `任务动态 · 当前步骤 ${plan.done}/${plan.total}` : "任务动态"}
          onClick={onTogglePanel}
        >
          <PanelRight size={16} />
          {(busy || waiting > 0) && <i className={`workbench-toggle-dot${!busy ? " waiting" : ""}`} aria-hidden="true" />}
        </button>
        {busy && <button className="stop-button" onClick={onStop}>停止</button>}
      </div>
    </div>
  );
}
