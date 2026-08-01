import { describe, expect, it } from "vitest";

import {
  FIRST_DELIVERY_PROMPT,
  PROJECT_BRIEF_PROMPT,
  firstDeliveryReadiness,
  isPlanExecutionConfirmation,
} from "./onboarding";

describe("firstDeliveryReadiness", () => {
  it("模型状态未返回时保持检查态，不能开始交付", () => {
    expect(firstDeliveryReadiness({
      modelLoaded: false,
      modelConfigured: false,
      hasProject: false,
      runtimeConnected: false,
    })).toEqual({
      model: "checking",
      project: "required",
      runtime: "waiting",
      canDraft: false,
    });
  });

  it("模型、项目与 runtime 全部就绪后才开放黄金任务", () => {
    expect(firstDeliveryReadiness({
      modelLoaded: true,
      modelConfigured: true,
      hasProject: true,
      runtimeConnected: true,
    })).toEqual({
      model: "ready",
      project: "ready",
      runtime: "ready",
      canDraft: true,
    });
  });

  it("项目已选但模型未配置时继续阻止黄金任务", () => {
    expect(firstDeliveryReadiness({
      modelLoaded: true,
      modelConfigured: false,
      hasProject: true,
      runtimeConnected: true,
    }).canDraft).toBe(false);
  });
});

describe("onboarding prompts", () => {
  it("首个交付在同一任务内经确认门从规划继续到隔离交付", () => {
    expect(FIRST_DELIVERY_PROMPT).toContain("同一任务");
    expect(FIRST_DELIVERY_PROMPT).toContain("内置确认门");
    expect(FIRST_DELIVERY_PROMPT).toContain("不要结束任务让我手动切换模式");
    expect(FIRST_DELIVERY_PROMPT).toContain("一次性授权");
    expect(FIRST_DELIVERY_PROMPT).toContain("隔离工作区");
    expect(FIRST_DELIVERY_PROMPT).toContain("验收证据");
    expect(FIRST_DELIVERY_PROMPT).toContain("Draft PR");
    expect(FIRST_DELIVERY_PROMPT).toContain("不要直接修改 main");
  });

  it("项目体检保持只读", () => {
    expect(PROJECT_BRIEF_PROMPT).toContain("只读");
    expect(PROJECT_BRIEF_PROMPT).toContain("不要修改文件");
  });
});

describe("isPlanExecutionConfirmation", () => {
  it("只把 runtime 生成的 plan 执行授权识别为连续交付确认", () => {
    expect(isPlanExecutionConfirmation(
      "plan 阶段分析已完成，需要你授权才能动手。\n原因：计划已就绪",
    )).toBe(true);
    expect(isPlanExecutionConfirmation("删除文件？此操作不可逆。")).toBe(false);
  });
});
