import { describe, expect, it } from "vitest";

import { isPlanBudgetConfirmation, isPlanExecutionConfirmation } from "./onboarding";

describe("isPlanExecutionConfirmation", () => {
  it("只把 runtime 生成的 plan 执行授权识别为连续交付确认", () => {
    expect(isPlanExecutionConfirmation(
      "plan 阶段分析已完成，需要你授权才能动手。\n原因：计划已就绪",
    )).toBe(true);
    expect(isPlanExecutionConfirmation("删除文件？此操作不可逆。")).toBe(false);
    const budget = "plan 阶段预算已到，尚未形成可审阅计划；继续需要授权。\n原因：plan 阶段单段执行预算已到";
    expect(isPlanExecutionConfirmation(budget)).toBe(true);
    expect(isPlanBudgetConfirmation(budget)).toBe(true);
    expect(isPlanBudgetConfirmation("plan 阶段分析已完成，需要你授权才能动手。 ")).toBe(false);
  });
});
