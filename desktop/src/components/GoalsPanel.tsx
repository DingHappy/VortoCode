// App.tsx 抽出的「完成」（goals）inspector 面板（B8-④c S9，只搬家不改行为）。
//
// 纯展示件契约（同 S3–S7）：goals 喂 sidebar 角标（activeGoals 派生留 App）、被总线经
// refreshGoals 续期；tasks/runs 是跨域只读（goal 卡内推「后台任务执行中/自动验收执行中」）；
// 目标合同表单草稿（objective/criteria/constraints/nonGoals + 两组逐条草稿）今天都是
// App 级持有、切 tab 不丢，且被 saveGoalDraft/saveGoalVerifier/recordGoalEvidence 消费
// ——全部留 App 经 props 下传，本面板无任何自持状态。
// goalFormLines/goalEvidenceKey/criterionVerifierDraft 与 GoalVerifierDraft 上收 lib/goals
// （App 写路径与本面板共用）。
import type { Dispatch, SetStateAction } from "react";

import { statusLabel, verifierKindLabel } from "../lib/labels";
import { criterionVerifierDraft, goalEvidenceKey, goalFormLines } from "../lib/goals";
import type { GoalVerifierDraft } from "../lib/goals";
import type {
  CommandRunItem,
  ConnectionState,
  GoalCriterion,
  GoalItem,
  GoalVerifierKind,
  TaskItem,
} from "../types";

type GoalsPanelProps = {
  goals: GoalItem[];
  tasks: TaskItem[];
  runs: CommandRunItem[];
  connection: ConnectionState;
  editingGoalId: string | null;
  goalObjective: string;
  goalCriteria: string;
  goalConstraints: string;
  goalNonGoals: string;
  goalSubmitting: boolean;
  goalEvidenceDrafts: Record<string, string>;
  goalVerifierDrafts: Record<string, GoalVerifierDraft>;
  onObjectiveChange: (value: string) => void;
  onCriteriaChange: (value: string) => void;
  onConstraintsChange: (value: string) => void;
  onNonGoalsChange: (value: string) => void;
  onEvidenceDraftsChange: Dispatch<SetStateAction<Record<string, string>>>;
  onVerifierDraftsChange: Dispatch<SetStateAction<Record<string, GoalVerifierDraft>>>;
  onResetForm: () => void;
  onSaveDraft: () => void;
  onEditDraft: (goal: GoalItem) => void;
  onDeleteDraft: (goal: GoalItem) => void;
  onRunGoal: (goal: GoalItem, resume: boolean) => void;
  onRunVerifiers: (goal: GoalItem) => void;
  onSaveVerifier: (goal: GoalItem, criterion: GoalCriterion) => void;
  onRecordEvidence: (
    goal: GoalItem,
    criterion: GoalCriterion,
    passed: boolean,
    adopted?: { kind: string; summary: string },
  ) => void;
};

export function GoalsPanel({
  goals,
  tasks,
  runs,
  connection,
  editingGoalId,
  goalObjective,
  goalCriteria,
  goalConstraints,
  goalNonGoals,
  goalSubmitting,
  goalEvidenceDrafts,
  goalVerifierDrafts,
  onObjectiveChange,
  onCriteriaChange,
  onConstraintsChange,
  onNonGoalsChange,
  onEvidenceDraftsChange,
  onVerifierDraftsChange,
  onResetForm,
  onSaveDraft,
  onEditDraft,
  onDeleteDraft,
  onRunGoal,
  onRunVerifiers,
  onSaveVerifier,
  onRecordEvidence,
}: GoalsPanelProps) {
  return (
    <div className="goals-panel">
      <div className="goal-form">
        <div className="goal-form-heading">
          <strong>{editingGoalId ? "编辑目标草稿" : "新建目标合同"}</strong>
          <span>验收有证据才算达成</span>
        </div>
        <label>
          <span>目标</span>
          <textarea value={goalObjective} onChange={(event) => onObjectiveChange(event.target.value)} placeholder="要交付的可验证结果，而不是笼统动作…" />
        </label>
        <label>
          <span>验收标准 <em>每行一条</em></span>
          <textarea value={goalCriteria} onChange={(event) => onCriteriaChange(event.target.value)} placeholder={"测试与构建通过\nDesktop 可以创建并逐项验收目标"} />
        </label>
        <details className="goal-contract-options">
          <summary>约束与非目标</summary>
          <label>
            <span>约束 <em>每行一条</em></span>
            <textarea value={goalConstraints} onChange={(event) => onConstraintsChange(event.target.value)} placeholder="复用现有 TaskRunner" />
          </label>
          <label>
            <span>非目标 <em>每行一条</em></span>
            <textarea value={goalNonGoals} onChange={(event) => onNonGoalsChange(event.target.value)} placeholder="不恢复无限自治循环" />
          </label>
        </details>
        <div className="goal-form-actions">
          {editingGoalId && <button onClick={onResetForm}>取消编辑</button>}
          <button className="goal-start" disabled={goalSubmitting || connection !== "connected" || !goalObjective.trim() || goalFormLines(goalCriteria).length === 0} onClick={() => void onSaveDraft()}>
            {editingGoalId ? "保存草稿修改" : "保存目标草稿"}
          </button>
        </div>
      </div>

      {goals.length === 0 && <div className="panel-empty compact"><strong>还没有开发目标</strong><p>目标会把执行计划、分支和逐项验收证据串在一起。</p></div>}
      {goals.map((goal) => {
        const activeTask = tasks.some((task) => task.goal_id === goal.id && ["queued", "running"].includes(task.status));
        const activeVerifier = runs.some((run) => run.goal_id === goal.id && ["queued", "running", "cancelling"].includes(run.status));
        const hasVerifiers = goal.acceptance_criteria.some((criterion) => Boolean(criterion.verifier));
        const progress = goal.progress ?? {
          passed: goal.acceptance_criteria.filter((criterion) => criterion.status === "passed").length,
          failed: goal.acceptance_criteria.filter((criterion) => criterion.status === "failed").length,
          total: goal.acceptance_criteria.length,
        };
        const automaticEvidence = [...goal.evidence].reverse().find((evidence) => (
          !evidence.criterion_id && evidence.passed && ["test", "review"].includes(evidence.kind)
        ));
        return (
          <section className={`goal-card ${goal.status}`} key={goal.id}>
            <div className="goal-card-head">
              <span className={`task-status ${goal.status}`}>{statusLabel(goal.status)}</span>
              <small>{progress.passed}/{progress.total} 已验收</small>
            </div>
            <h3>{goal.objective}</h3>
            <div className="goal-progress"><i style={{ width: `${progress.total ? (progress.passed / progress.total) * 100 : 0}%` }} /></div>
            {(goal.constraints.length > 0 || goal.non_goals.length > 0) && (
              <details className="goal-contract-summary" open={goal.status === "draft"}>
                <summary>合同边界</summary>
                {goal.constraints.length > 0 && (
                  <div><strong>约束</strong>{goal.constraints.map((item, index) => <p key={`constraint-${index}-${item}`}>• {item}</p>)}</div>
                )}
                {goal.non_goals.length > 0 && (
                  <div><strong>非目标</strong>{goal.non_goals.map((item, index) => <p key={`non-goal-${index}-${item}`}>• {item}</p>)}</div>
                )}
              </details>
            )}
            {goal.blocker && <div className="goal-blocker"><strong>阻塞</strong>{goal.blocker}</div>}
            <div className="goal-criteria">
              {goal.acceptance_criteria.map((criterion) => {
                const key = goalEvidenceKey(goal.id, criterion.id);
                const verifierDraft = goalVerifierDrafts[key] ?? criterionVerifierDraft(criterion);
                return (
                  <div className={`goal-criterion ${criterion.status}`} key={criterion.id}>
                    <div className="goal-criterion-title">
                      <span>{criterion.status === "passed" ? "✓" : criterion.status === "failed" ? "!" : "○"}</span>
                      <p>{criterion.text}</p>
                      {criterion.verifier && <em>{verifierKindLabel(criterion.verifier.kind)}</em>}
                    </div>
                    {goal.status === "draft" && (
                      <div className="goal-verifier-editor">
                        <select
                          aria-label={`${criterion.text} 验收方式`}
                          value={verifierDraft.kind}
                          onChange={(event) => onVerifierDraftsChange((previous) => ({
                            ...previous,
                            [key]: { ...verifierDraft, kind: event.target.value as GoalVerifierKind | "manual" },
                          }))}
                        >
                          <option value="manual">人工验收</option>
                          <option value="test">测试命令</option>
                          <option value="build">构建命令</option>
                          <option value="lint">Lint 命令</option>
                          <option value="file">文件检查</option>
                        </select>
                        {verifierDraft.kind !== "manual" && (
                          <input
                            value={verifierDraft.value}
                            onChange={(event) => onVerifierDraftsChange((previous) => ({
                              ...previous,
                              [key]: { ...verifierDraft, value: event.target.value },
                            }))}
                            placeholder={verifierDraft.kind === "file" ? "仓库内相对路径，如 dist/index.html" : "命令，如 npm test"}
                          />
                        )}
                        {verifierDraft.kind === "file" && (
                          <input
                            className="goal-verifier-contains"
                            value={verifierDraft.contains}
                            onChange={(event) => onVerifierDraftsChange((previous) => ({
                              ...previous,
                              [key]: { ...verifierDraft, contains: event.target.value },
                            }))}
                            placeholder="可选：文件必须包含的文本"
                          />
                        )}
                        {verifierDraft.kind !== "manual" && verifierDraft.kind !== "file" && (
                          <input
                            className="goal-verifier-timeout"
                            type="number"
                            min="1"
                            max="900"
                            value={verifierDraft.timeout}
                            onChange={(event) => onVerifierDraftsChange((previous) => ({
                              ...previous,
                              [key]: { ...verifierDraft, timeout: event.target.value },
                            }))}
                            title="超时秒数"
                          />
                        )}
                        <button
                          disabled={goalSubmitting || (verifierDraft.kind !== "manual" && !verifierDraft.value.trim())}
                          onClick={() => void onSaveVerifier(goal, criterion)}
                        >保存验收器</button>
                      </div>
                    )}
                    {goal.status !== "draft" && (
                      <div className="goal-evidence-entry">
                        <input
                          value={goalEvidenceDrafts[key] ?? ""}
                          onChange={(event) => onEvidenceDraftsChange((previous) => ({ ...previous, [key]: event.target.value }))}
                          placeholder="测试命令、截图或人工复核结论…"
                        />
                        <button className="fail" title="记录未通过" onClick={() => void onRecordEvidence(goal, criterion, false)}>未过</button>
                        <button className="pass" title="记录通过" onClick={() => void onRecordEvidence(goal, criterion, true)}>通过</button>
                        {automaticEvidence && criterion.status !== "passed" && (
                          <button
                            className="use-evidence"
                            title={automaticEvidence.summary}
                            onClick={() => void onRecordEvidence(goal, criterion, true, {
                              kind: automaticEvidence.kind,
                              summary: `采用自动${automaticEvidence.kind === "test" ? "测试" : "审查"}证据：${automaticEvidence.summary}`,
                            })}
                          >采用自动证据</button>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            {goal.evidence.length > 0 && (
              <details className="goal-evidence-log">
                <summary>{goal.evidence.length} 条证据</summary>
                {goal.evidence.slice(-4).reverse().map((evidence) => (
                  <p className={evidence.passed ? "passed" : "failed"} key={evidence.id}>
                    <b>{evidence.passed ? "通过" : "未过"}</b>{evidence.summary}
                  </p>
                ))}
              </details>
            )}
            {goal.branch && <code>{goal.branch}</code>}
            {goal.plan_id && <code className="goal-plan-id">plan · {goal.plan_id}</code>}
            {goal.next_action && <p className="goal-next">下一步：{goal.next_action}</p>}
            <div className="goal-actions">
              {activeTask && <span>后台任务执行中…</span>}
              {!activeTask && activeVerifier && <span>自动验收执行中…</span>}
              {goal.status === "draft" && (
                <>
                  <button onClick={() => onEditDraft(goal)} disabled={goalSubmitting}>编辑</button>
                  <button onClick={() => void onDeleteDraft(goal)} disabled={goalSubmitting}>删除</button>
                  <button className="primary" onClick={() => void onRunGoal(goal, false)} disabled={goalSubmitting}>确认并开始执行</button>
                </>
              )}
              {goal.status !== "draft" && !activeTask && goal.status !== "achieved" && <button onClick={() => void onRunGoal(goal, false)} disabled={goalSubmitting}>新一轮执行</button>}
              {goal.status !== "draft" && !activeTask && goal.status !== "achieved" && hasVerifiers && <button className="primary" onClick={() => void onRunVerifiers(goal)} disabled={goalSubmitting || activeVerifier}>运行自动验收</button>}
              {goal.status !== "draft" && !activeTask && goal.status === "blocked" && goal.plan_id && <button className="primary" onClick={() => void onRunGoal(goal, true)} disabled={goalSubmitting}>断点续跑</button>}
            </div>
          </section>
        );
      })}
    </div>
  );
}
