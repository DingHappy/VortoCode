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


// Read-only exact navigation; a target is never substituted with the first list entry.
export type GoalFocus = { id: string; status: "loading" | "ready" | "error"; goal?: import("../types").GoalItem; error?: string };
export function createGoalNavigation(
  currentClient: () => { getGoal(id: string): Promise<import("../types").GoalItem> } | null,
  publish: (focus: GoalFocus | null) => void,
) {
  let generation = 0;
  let selectedId = "";
  return {
    reset() { generation += 1; selectedId = ""; publish(null); },
    clearDeleted(id: string) { if (selectedId === id) { generation += 1; selectedId = ""; publish(null); } },
    async focus(id: string) {
      const attempt = ++generation;
      selectedId = id;
      const client = currentClient();
      publish({ id, status: "loading" });
      const current = () => attempt === generation && currentClient() === client;
      try {
        if (!client) throw new Error("尚未连接来源项目，请重新打开目标");
        const goal = await client.getGoal(id);
        if (!current()) return;
        if (goal.id !== id) throw new Error("目标响应身份不匹配，请重新核对来源");
        publish({ id, status: "ready", goal });
      } catch (error) {
        if (current()) publish({ id, status: "error", error: error instanceof Error ? error.message : "读取目标失败" });
      }
    },
  };
}
