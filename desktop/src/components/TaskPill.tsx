// 顶栏右上角的任务胶囊（docs/DESIGN_SYSTEM.md 第 4 节）：平时中性色写「N 件在忙」，
// 有事等你处理时变成琥珀色「N 件等你 · M 件在忙」，点击去看任务。
import { joinClass } from "./ui";

export function taskPillText(waiting: number, busy: number, uncertain = false): string {
  const parts = [
    waiting ? `${waiting}${uncertain ? "+" : ""} 件等你` : "",
    busy ? `${busy} 件在忙` : "",
    // 有来源没读到、也没读到等你的事：不能说「没事」，只说待核对，不变色
    !waiting && uncertain ? "待核对" : "",
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "任务";
}

export function TaskPill({ waiting, busy, uncertain = false, open = false, onOpen }: {
  waiting: number;
  busy: number;
  /** 有来源没读到：等你的件数可能更多 */
  uncertain?: boolean;
  /** 任务正在打开 */
  open?: boolean;
  onOpen: () => void;
}) {
  const attention = waiting > 0;
  return (
    <button
      type="button"
      className={joinClass("vc-pill", "vc-task-pill", attention && "vc-pill--wait")}
      aria-expanded={open}
      title={uncertain ? "部分来源没读到，等你处理的可能不止这些" : "查看任务"}
      onClick={onOpen}
    >
      {(attention || busy > 0) && <span className="vc-pill__dot" aria-hidden="true" />}
      {taskPillText(waiting, busy, uncertain)}
    </button>
  );
}
