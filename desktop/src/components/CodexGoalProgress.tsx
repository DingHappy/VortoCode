import type { CodexGoalProgress as Progress } from "../lib/codexGoalSupervision";
import "./CodexGoalProgress.css";

const LABEL: Record<Progress["state"], string> = {
  active: "跟进中", paused: "跟进已暂停", awaiting_user: "等你处理", confirmed: "已有验收记录", unknown: "待核对",
};

/** Only navigates to the original goal. No approval, budget or execution mutation. */
export function CodexGoalProgress({ progress, onOpenGoal }: { progress: Progress; onOpenGoal: (id: string) => void }) {
  return <section className={`codex-goal-progress ${progress.state}`} aria-label="关联目标跟进">
    <header><strong>{progress.title}</strong><span>{LABEL[progress.state]}</span></header>
    <p>{progress.reason}</p>
    {progress.used !== undefined && progress.limit !== undefined && <p className="codex-goal-allowance">
      {progress.state === "unknown" ? "上次记录：" : ""}已判断 {progress.used} / {progress.limit} 次
    </p>}
    {progress.criteria.length > 0 && <details><summary>本次跟进范围</summary><ul>
      {progress.criteria.map((text, i) => <li key={i}>{text}</li>)}
    </ul></details>}
    <p className="codex-goal-note">跟进暂停不代表 Codex 已停止；目标仍按原有达标条件验收。</p>
    <button disabled={!progress.canOpenGoal} onClick={() => onOpenGoal(progress.goalId)}>核对目标与验收</button>
  </section>;
}
