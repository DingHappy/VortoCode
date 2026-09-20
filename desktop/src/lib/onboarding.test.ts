import { describe, expect, it } from "vitest";

import {
  FIRST_DELIVERY_PROMPT,
  PROJECT_BRIEF_PROMPT,
  firstDeliveryReadiness,
  isPlanExecutionConfirmation,
  shouldShowOnboardingChecklist,
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

describe("shouldShowOnboardingChecklist", () => {
  const ready = firstDeliveryReadiness({
    modelLoaded: true, modelConfigured: true, hasProject: true, runtimeConnected: true,
  });

  it("三步全绿后收起清单（否则它就是常驻噪音）", () => {
    expect(ready.canDraft).toBe(true);
    expect(shouldShowOnboardingChecklist(ready)).toBe(false);
  });

  it("还没配模型 / 没选项目 / runtime 没就绪时仍然显示", () => {
    const cases = [
      { modelLoaded: true, modelConfigured: false, hasProject: true, runtimeConnected: true },
      { modelLoaded: true, modelConfigured: true, hasProject: false, runtimeConnected: true },
      { modelLoaded: true, modelConfigured: true, hasProject: true, runtimeConnected: false },
      { modelLoaded: false, modelConfigured: true, hasProject: true, runtimeConnected: true },
    ];
    for (const input of cases) {
      expect(shouldShowOnboardingChecklist(firstDeliveryReadiness(input))).toBe(true);
    }
  });
});
