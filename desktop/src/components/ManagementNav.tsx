// 工作台「任务」「目标」整页的页头：标题、一句说明、返回对话；「任务」下面再分看板 / 后台任务 / 待处理（画布 E13）。
import { X } from "lucide-react";
import type { InspectorTab } from "../lib/inspector";
import { IconButton } from "./ui";

const TASK_TABS: Array<[InspectorTab, string]> = [["inbox", "看板"], ["tasks", "后台任务"], ["decisions", "待处理"]];

export function ManagementNav({ current, onSelect }: { current: InspectorTab; onSelect: (tab: InspectorTab) => void }) {
  return <nav className="vc-work-tabs" aria-label="任务视图">{TASK_TABS.map(([tab, label]) =>
    <button key={tab} type="button" aria-current={current === tab ? "page" : undefined} className={current === tab ? "active" : ""}
      onClick={() => onSelect(tab)}>{label}</button>)}</nav>;
}

export function ManagementHead({ current, onClose }: { current: InspectorTab; onClose: () => void }) {
  const goals = current === "goals";
  return <header className="vc-page-head vc-work-head">
    <div>
      <h1>{goals ? "目标" : "任务"}</h1>
      <p>{goals ? "项目目标和完成标准，以及每条目标下的任务。" : "派出去的活、会话和等你确认的事，按状态排。"}</p>
    </div>
    <IconButton label="返回对话" icon={<X />} onClick={onClose} />
  </header>;
}
