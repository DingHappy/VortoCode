import { expect, it } from "vitest";
import { personalGoalScopeInput } from "./personalGoalScope";

it("keeps restrictions and deferrals as explicit ordered goal data", () => {
  expect(personalGoalScopeInput("  仅文档\n\n不运行付费接口  ", "测试留到下一轮")).toEqual({
    constraints: ["仅文档", "不运行付费接口"], non_goals: ["测试留到下一轮"], error: "",
  });
  expect(personalGoalScopeInput("", "")).toEqual({ constraints: [], non_goals: [], error: "" });
});

it.each([
  [Array(21).fill("限制").join("\n"), "", "20 条"],
  ["", "x".repeat(1001), "1000 字符"],
  [Array(8).fill("x".repeat(1000)).join("\n"), "再一个", "8000 字符"],
  ["不能\0丢失", "", "1000 字符"],
])("reports invalid scope without silently truncating it", (constraints, nonGoals, message) => {
  const result = personalGoalScopeInput(constraints, nonGoals);
  expect(result.error).toContain(message);
  expect(result.constraints.join("\n")).toBe(constraints);
  expect(result.non_goals.join("\n")).toBe(nonGoals);
});
