// goal 合同表单的纯辅助（B8-④c S9 从 App.tsx 上收）：saveGoalDraft/saveGoalVerifier/
// recordGoalEvidence（App 侧写路径）与 <GoalsPanel>（渲染侧）共用，故收进 lib 供两端导入。
import type { GoalCriterion, GoalVerifierKind } from "../types";

export type GoalVerifierDraft = {
  kind: GoalVerifierKind | "manual";
  value: string;
  contains: string;
  timeout: string;
};

export function goalFormLines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim().replace(/^[-*]\s*/, ""))
    .filter(Boolean);
}

export function goalEvidenceKey(goalId: string, criterionId: string): string {
  return `${goalId}:${criterionId}`;
}

export function criterionVerifierDraft(criterion: GoalCriterion): GoalVerifierDraft {
  const verifier = criterion.verifier;
  return {
    kind: verifier?.kind ?? "manual",
    value: verifier?.kind === "file" ? verifier.path ?? "" : verifier?.command ?? "",
    contains: verifier?.contains ?? "",
    timeout: String(verifier?.timeout ?? 300),
  };
}
