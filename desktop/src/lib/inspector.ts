export type InspectorTab =
  | "info" | "inbox" | "preview" | "files" | "diff" | "runs" | "goals" | "tasks" | "decisions" | "project";

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

/** Management pages are application navigation, never conversation inspectors. */
export const isManagementTab = (tab: InspectorTab) => ["inbox", "tasks", "goals", "decisions"].includes(tab);

/** 打开一个会话工具：已在标签栏里就原位不动，否则追加到末尾。任务中心的页面不进标签栏。 */
export function openWorkbenchTab(tabs: readonly InspectorTab[], tab: InspectorTab): InspectorTab[] {
  if (isManagementTab(tab) || tabs.includes(tab)) return [...tabs];
  return [...tabs, tab];
}

/**
 * 关掉一个标签。返回剩下的标签，以及接下来该看哪个：
 * 关的不是当前标签就不换；关的是当前标签就看右边那个，没有右边看左边；全关完返回 null（回到工具列表）。
 */
export function closeWorkbenchTab(
  tabs: readonly InspectorTab[], closing: InspectorTab, current: InspectorTab,
): { tabs: InspectorTab[]; next: InspectorTab | null } {
  const index = tabs.indexOf(closing);
  if (index < 0) return { tabs: [...tabs], next: current };
  const rest = tabs.filter((tab) => tab !== closing);
  if (closing !== current) return { tabs: rest, next: current };
  return { tabs: rest, next: rest[Math.min(index, rest.length - 1)] ?? null };
}
