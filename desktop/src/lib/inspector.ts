export type InspectorTab =
  | "inbox" | "files" | "diff" | "runs" | "goals" | "tasks" | "decisions" | "project";

/**
 * Agent 那边发生的事要不要切走你正在看的标签。
 *
 * 真机 2026-09-17：正在看「代码」，一个 agent 事件把面板切到了别处——用户的当前视线不该
 * 被后台动静抢走。规则：面板没开就打开（此时没有"正在看的东西"）；开着且正好在目标页就
 * 什么都不做；开着但在看别的，只标一个提示点，由人决定什么时候过去。
 */
export function autoFocusDecision(input: {
  open: boolean;
  current: InspectorTab;
  target: InspectorTab;
}): "open" | "ignore" | "mark" {
  if (!input.open) return "open";
  if (input.current === input.target) return "ignore";
  return "mark";
}
