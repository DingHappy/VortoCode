const PLAN_EXECUTION_CONFIRMATION_PREFIX = "plan 阶段分析已完成，需要你授权才能动手。";
const PLAN_BUDGET_CONFIRMATION_PREFIX = "plan 阶段预算已到，尚未形成可审阅计划；继续需要授权。";

export function isPlanExecutionConfirmation(text: string): boolean {
  const confirmation = text.trimStart();
  return confirmation.startsWith(PLAN_EXECUTION_CONFIRMATION_PREFIX)
    || confirmation.startsWith(PLAN_BUDGET_CONFIRMATION_PREFIX);
}

export function isPlanBudgetConfirmation(text: string): boolean {
  return text.trimStart().startsWith(PLAN_BUDGET_CONFIRMATION_PREFIX);
}
