// 默认提供安静的摘要入口；待决定、异常与来源未知保持显式提示。
import { joinClass } from "./ui";

export function taskPillText(waiting: number, _busy: number, uncertain = false, exceptions = 0): string {
  const parts = [
    waiting ? `${waiting}${uncertain ? "+" : ""} 件等你` : "",
    exceptions ? `${exceptions}${uncertain ? "+" : ""} 项需核对` : "",
    // 有来源没读到、也没读到等你的事：不能说「没事」，只说待核对，不变色
    !waiting && !exceptions && uncertain ? "待核对" : "",
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "查看摘要";
}

export function TaskPill({ waiting, uncertain = false, exceptions = 0, open = false, onOpen }: {
  waiting: number;
  busy: number;
  exceptions?: number;
  /** 有来源没读到：等你的件数可能更多 */
  uncertain?: boolean;
  /** 任务正在打开 */
  open?: boolean;
  onOpen: () => void;
}) {
  const attention = waiting > 0 || exceptions > 0;
  return (
    <button
      type="button"
      className={joinClass("vc-pill", "vc-task-pill", attention && "vc-pill--wait")}
      aria-expanded={open}
      title={uncertain ? "部分来源没读到，等你处理的可能不止这些" : "查看工作摘要与待处理事项"}
      onClick={onOpen}
    >
      {attention && <span className="vc-pill__dot" aria-hidden="true" />}
      {taskPillText(waiting, 0, uncertain, exceptions)}
    </button>
  );
}
