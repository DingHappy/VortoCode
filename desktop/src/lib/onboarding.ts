export const FIRST_DELIVERY_PROMPT = [
  "请先只读分析当前项目，选择一个低风险、范围小、能够自动验证的改进。",
  "请在同一任务中先给出目标、约束、非目标、验收标准和实施计划。",
  "计划准备好后，通过内置确认门请求我授权执行；不要结束任务让我手动切换模式。",
  "获得一次性授权后，请继续在隔离工作区完成实现、运行测试、做代码审查，并准备一个带验收证据的 Draft PR；不要直接修改 main。",
].join("");

export const PROJECT_BRIEF_PROMPT = [
  "请只读了解当前项目，告诉我它解决什么问题、如何运行和测试、目前最值得处理的三个风险，",
  "并推荐一个范围小、能够自动验证的首个交付任务。不要修改文件。",
].join("");

export type FirstDeliveryReadiness = {
  model: "checking" | "required" | "ready";
  project: "required" | "ready";
  runtime: "waiting" | "ready";
  canDraft: boolean;
};

const PLAN_EXECUTION_CONFIRMATION_PREFIX = "plan 阶段分析已完成，需要你授权才能动手。";

export function isPlanExecutionConfirmation(text: string): boolean {
  return text.trimStart().startsWith(PLAN_EXECUTION_CONFIRMATION_PREFIX);
}

export function firstDeliveryReadiness(input: {
  modelLoaded: boolean;
  modelConfigured: boolean;
  hasProject: boolean;
  runtimeConnected: boolean;
}): FirstDeliveryReadiness {
  const model = !input.modelLoaded
    ? "checking"
    : input.modelConfigured
      ? "ready"
      : "required";
  const project = input.hasProject ? "ready" : "required";
  const runtime = input.hasProject && input.runtimeConnected ? "ready" : "waiting";

  return {
    model,
    project,
    runtime,
    canDraft: model === "ready" && project === "ready" && runtime === "ready",
  };
}
