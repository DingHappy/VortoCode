// Goal 域 hook（沿用 useJournal 的 hook 模式与三条红线）。
//
// 收进来的是只写 goal 自身状态的部分：列表、合同表单、证据/验收器草稿，以及对应读写回调。
// 留在 App 的是跨域写入：runGoal 写任务域（captureTaskScope/upsertTask）、runGoalVerifiers
// 写 runs 域与 inspector、adoptRunEvidence 本属 runs 域——它们用这里归还的 setGoals /
// setGoalSubmitting，dispatcher 仍只有 App 一个（红线 1），hook 不是第二真相源（红线 2）。
//
// 红线 3：refreshGoals 被 handleProtocolEvent / refreshAllForScope 的 useCallback 依赖，必须恒等
// 稳定（依赖只有恒等的 clientRef）。其余处理函数迁移前在 App 里就是每次渲染新建的普通函数，
// 纯移动保持原样，不新增 useCallback。
//
// 跨域触点：结果提示走注入的 onBanner；保存/编辑后切到目标面板走注入的 onShowGoals。
import { useCallback, useState } from "react";
import type { RefObject } from "react";

import type { GatewayClient } from "../gateway";
import { errorText } from "../lib/errorText";
import { criterionVerifierDraft, goalEvidenceKey, goalFormLines } from "../lib/goals";
import type { GoalVerifierDraft } from "../lib/goals";
import type { GoalCriterion, GoalItem } from "../types";

export function useGoals(
  clientRef: RefObject<GatewayClient | null>,
  onBanner: (text: string) => void,
  onShowGoals: () => void,
) {
  const [goals, setGoals] = useState<GoalItem[]>([]);
  const [goalObjective, setGoalObjective] = useState("");
  const [goalCriteria, setGoalCriteria] = useState("");
  const [goalConstraints, setGoalConstraints] = useState("");
  const [goalNonGoals, setGoalNonGoals] = useState("");
  const [goalEvidenceDrafts, setGoalEvidenceDrafts] = useState<Record<string, string>>({});
  const [goalVerifierDrafts, setGoalVerifierDrafts] = useState<Record<string, GoalVerifierDraft>>({});
  const [editingGoalId, setEditingGoalId] = useState<string | null>(null);
  const [goalSubmitting, setGoalSubmitting] = useState(false);

  const refreshGoals = useCallback(async () => {
    const client = clientRef.current;
    if (!client) return;
    try {
      setGoals(await client.listGoals());
    } catch {
      // Goal 面板不是连接握手的硬依赖；旧 runtime 可继续使用会话与任务面。
    }
  }, [clientRef]);

  const resetGoalForm = () => {
    setEditingGoalId(null);
    setGoalObjective("");
    setGoalCriteria("");
    setGoalConstraints("");
    setGoalNonGoals("");
  };

  const saveGoalDraft = async () => {
    const objective = goalObjective.trim();
    const acceptanceCriteria = goalFormLines(goalCriteria);
    if (!objective || acceptanceCriteria.length === 0 || !clientRef.current) {
      onBanner("创建目标需要目标描述和至少一条验收标准");
      return;
    }
    setGoalSubmitting(true);
    try {
      const input = {
        objective,
        acceptance_criteria: acceptanceCriteria,
        constraints: goalFormLines(goalConstraints),
        non_goals: goalFormLines(goalNonGoals),
        start: false,
      };
      const saved = editingGoalId
        ? await clientRef.current.updateGoal(editingGoalId, input)
        : await clientRef.current.createGoal(input);
      setGoals((previous) => [saved, ...previous.filter((goal) => goal.id !== saved.id)]);
      resetGoalForm();
      onShowGoals();
      onBanner(editingGoalId ? "目标合同草稿已更新，请确认后开始执行" : "目标合同已保存为草稿，请检查后确认执行");
      await refreshGoals();
    } catch (error) {
      onBanner(errorText(error, "目标草稿保存失败"));
    } finally {
      setGoalSubmitting(false);
    }
  };

  const editGoalDraft = (goal: GoalItem) => {
    setEditingGoalId(goal.id);
    setGoalObjective(goal.objective);
    setGoalCriteria(goal.acceptance_criteria.map((criterion) => criterion.text).join("\n"));
    setGoalConstraints((goal.constraints ?? []).join("\n"));
    setGoalNonGoals((goal.non_goals ?? []).join("\n"));
    onShowGoals();
  };

  const deleteGoalDraft = async (goal: GoalItem) => {
    if (!clientRef.current || !window.confirm(`删除目标草稿“${goal.objective}”？`)) return;
    try {
      await clientRef.current.deleteGoal(goal.id);
      setGoals((previous) => previous.filter((item) => item.id !== goal.id));
      if (editingGoalId === goal.id) resetGoalForm();
      onBanner("目标草稿已删除");
    } catch (error) {
      onBanner(errorText(error, "目标草稿删除失败"));
    }
  };

  const recordGoalEvidence = async (
    goal: GoalItem,
    criterion: GoalCriterion,
    passed: boolean,
    adopted?: { kind: string; summary: string; evidence_id: string },
  ) => {
    if (!clientRef.current) return;
    const key = goalEvidenceKey(goal.id, criterion.id);
    const summary = adopted?.summary.trim() || (goalEvidenceDrafts[key] ?? "").trim();
    if (!summary) {
      onBanner("请先为这条验收标准填写可复核的证据摘要");
      return;
    }
    try {
      const updated = await clientRef.current.recordGoalEvidence(goal.id, criterion.id, {
        passed,
        summary,
        kind: adopted?.kind || "manual",
        evidence_id: adopted?.evidence_id,
      });
      setGoals((previous) => [updated, ...previous.filter((item) => item.id !== goal.id)]);
      setGoalEvidenceDrafts((previous) => ({ ...previous, [key]: "" }));
      const accepted = updated.acceptance_criteria.find((item) => item.id === criterion.id)?.status;
      onBanner(updated.status === "achieved" ? "所有验收标准均有通过证据，目标已达成" : accepted === "pending" ? "证据未对应当前目标版本，请重新验收" : passed ? "通过证据已记录" : "失败证据已记录，目标进入阻塞状态");
    } catch (error) {
      onBanner(errorText(error, "记录验收证据失败"));
    }
  };

  const saveGoalVerifier = async (goal: GoalItem, criterion: GoalCriterion) => {
    if (!clientRef.current) return;
    const key = goalEvidenceKey(goal.id, criterion.id);
    const draft = goalVerifierDrafts[key] ?? criterionVerifierDraft(criterion);
    setGoalSubmitting(true);
    try {
      const timeout = Number.parseInt(draft.timeout || "300", 10);
      const updated = await clientRef.current.configureGoalVerifier(
        goal.id,
        criterion.id,
        draft.kind === "manual"
          ? { kind: "manual" }
          : draft.kind === "file"
            ? { kind: "file", path: draft.value.trim(), contains: draft.contains, timeout }
            : { kind: draft.kind, command: draft.value.trim(), timeout },
      );
      setGoals((previous) => [updated, ...previous.filter((item) => item.id !== goal.id)]);
      setGoalVerifierDrafts((previous) => ({
        ...previous,
        [key]: criterionVerifierDraft(
          updated.acceptance_criteria.find((item) => item.id === criterion.id) ?? criterion,
        ),
      }));
      onBanner(draft.kind === "manual" ? "该标准已改为人工验收" : "自动验收器已保存到目标合同");
    } catch (error) {
      onBanner(errorText(error, "保存自动验收器失败"));
    } finally {
      setGoalSubmitting(false);
    }
  };

  return {
    goals, setGoals, refreshGoals,
    goalObjective, setGoalObjective, goalCriteria, setGoalCriteria,
    goalConstraints, setGoalConstraints, goalNonGoals, setGoalNonGoals,
    goalEvidenceDrafts, setGoalEvidenceDrafts, goalVerifierDrafts, setGoalVerifierDrafts,
    editingGoalId, goalSubmitting, setGoalSubmitting,
    resetGoalForm, saveGoalDraft, editGoalDraft, deleteGoalDraft, recordGoalEvidence, saveGoalVerifier,
  };
}
