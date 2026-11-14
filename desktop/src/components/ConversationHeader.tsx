// 对话区标题栏：会话名、在哪台电脑上做（登记了常驻电脑时）、右侧面板开关或任务胶囊、停止按钮。只负责显示。
import type { ReactNode, RefObject } from "react";
import type { AssistantAvatar } from "../types";
import { RoleAvatar } from "./RoleAvatar";
import { Hash, Laptop, PanelRightClose, PanelRightOpen, Server, Square } from "lucide-react";
import { HintButton } from "./ui";

export function ConversationHeader({ title, roleIdentity, channelTopic, place, panelOpen, panelLabel = "任务动态", panelId, panelButtonRef, waiting, waitingUncertain = false, plan, busy, taskPill, status, onTogglePanel, onStop }: {
  title: string;
  roleIdentity?: { name: string; avatar?: AssistantAvatar | null; onOpen: () => void };
  channelTopic?: string;
  /** 登记了常驻电脑、又在工作台项目里时才显示 */
  place: "resident" | "local" | null;
  panelOpen: boolean;
  panelLabel?: string;
  panelId?: string;
  panelButtonRef?: RefObject<HTMLButtonElement | null>;
  /** 等你处理的件数 */
  waiting: number;
  waitingUncertain?: boolean;
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
      {roleIdentity ? <button type="button" className="vc-conversation-role" aria-haspopup="dialog" aria-label={`角色资料：${roleIdentity.name}`} onClick={roleIdentity.onOpen}>
        <RoleAvatar character="general" avatar={roleIdentity.avatar} /><span><strong>{roleIdentity.name}</strong><small>{title}</small></span>
      </button> : <div>
        <div className="conversation-channel-name">{channelTopic !== undefined && <Hash aria-label="频道" />}<strong title={title}>{title}</strong></div>
        {channelTopic && <span className="conversation-channel-topic" title={channelTopic}>{channelTopic}</span>}
        {place && (
          <span className={`conversation-place ${place}`}>
            {place === "resident" ? <><Server size={12} aria-hidden="true" />常驻电脑</> : <><Laptop size={12} aria-hidden="true" />本机</>}
          </span>)}
      </div>}
      <div className="conversation-actions">
        {status}
        {taskPill ?? <HintButton
          buttonRef={panelButtonRef}
          className={`context-toggle workbench-toggle icon-only ${panelOpen ? "active" : ""}`}
          aria-expanded={panelOpen}
          aria-controls={panelOpen ? panelId : undefined}
          aria-label={`${panelOpen ? "收起" : "打开"}${panelLabel}`}
          hint={`${panelOpen ? "收起" : "打开"}${panelLabel}${waiting ? ` · ${waiting}${waitingUncertain ? "+" : ""} 件等你处理` : waitingUncertain ? " · 待处理来源尚未完整读取" : plan.total ? ` · 当前步骤 ${plan.done}/${plan.total}` : ""}`}
          onClick={onTogglePanel}
        >
          {panelOpen ? <PanelRightClose size={16} aria-hidden="true" /> : <PanelRightOpen size={16} aria-hidden="true" />}
          {(busy || waiting > 0) && <i className={`workbench-toggle-dot${!busy ? " waiting" : ""}`} aria-hidden="true" />}
        </HintButton>}
        {busy && <button type="button" className="stop-button" onClick={onStop}><Square size={14} aria-hidden="true" />停止</button>}
      </div>
    </div>
  );
}
