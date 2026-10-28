// 状态胶囊与统一状态词（docs/DESIGN_SYSTEM.md 第 7 节）：不同界面里同一种状态说同一个词、用同一种颜色。
import type { ReactNode } from "react";
import { joinClass } from "./Button";

export type UiStatus = "wait" | "busy" | "done" | "unknown" | "failed";

export const STATUS_WORDS: Record<UiStatus, string> = {
  wait: "等你",
  busy: "在忙",
  done: "办好了",
  unknown: "无法确认",
  failed: "失败",
};

type StatusPillProps = {
  status: UiStatus;
  // 同一状态的具体说法，比如「等你点头」「第 2/5 步」「已送达」；不传就用统一状态词。
  children?: ReactNode;
  dot?: boolean;
  className?: string;
};

export function StatusPill({ status, children, dot = false, className }: StatusPillProps) {
  return (
    <span className={joinClass("vc-pill", `vc-pill--${status}`, className)}>
      {dot && <span className="vc-pill__dot" aria-hidden="true" />}
      {children ?? STATUS_WORDS[status]}
    </span>
  );
}

export function Tag({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={joinClass("vc-pill", className)}>{children}</span>;
}
