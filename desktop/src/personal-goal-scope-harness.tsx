/** Dev-only, in-memory interaction fixture. No gateway, model or disk writes. */
import { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { AssistantGoals } from "./components/AssistantGoals";
import type { PersonalGoal } from "./types";
import type { PersonalGoalClient } from "./lib/personalGoals";
import { applyThemePreference } from "./lib/theme";
import "./styles/tokens.css";
import "./App.css";
import "./daily.css";
import "./styles/shell.css";

applyThemePreference(new URLSearchParams(location.search).get("theme") === "dark" ? "dark" : "light");
document.body.style.margin = "0";

const initial: PersonalGoal = { id: "personal-scope-demo", kind: "personal", revision: 1, scope_revision: 1,
  objective: "整理项目说明", constraints: ["本阶段只写文档，不改代码", "不调用付费接口"], non_goals: ["测试与部署留到下一轮"],
  status: "active", task_ids: [], next_action: "核对说明", blocker: "", due_at: "", follow_up_at: "", overdue: false, follow_up_due: false,
  acceptance_criteria: [{ id: "criterion-1", text: "说明经用户验收", status: "passed", evidence_ids: ["e1"] },
    { id: "criterion-2", text: "范围确认", status: "pending", evidence_ids: [] }],
  evidence: [{ id: "e1", criterion_id: "criterion-1", kind: "personal_acceptance", source: "user_confirmation", summary: "旧范围已核对", passed: true }],
  progress: { passed: 1, failed: 0, total: 2 },
};

function Harness() {
  const [connected, setConnected] = useState(true);
  const [writes, setWrites] = useState(0);
  const client = useMemo(() => {
    let goals = [structuredClone(initial)];
    const unsupported = async (): Promise<PersonalGoal> => { throw new Error("夹具仅支持保存范围与新建"); };
    return {
      listPersonalGoals: async () => structuredClone(goals),
      createPersonalGoal: async input => {
        const goal: PersonalGoal = { ...structuredClone(initial), ...input, id: `personal-${input.request_id}`,
          revision: 1, scope_revision: input.constraints?.length || input.non_goals?.length ? 1 : 0,
          constraints: input.constraints ?? [], non_goals: input.non_goals ?? [], evidence: [],
          acceptance_criteria: input.acceptance_criteria.map((text, index) => ({ id: `criterion-${index+1}`, text, status: "pending", evidence_ids: [] })),
          progress: { passed: 0, failed: 0, total: input.acceptance_criteria.length }, due_at: input.due_at ?? "", follow_up_at: input.follow_up_at ?? "" };
        goals.push(goal); setWrites(n => n + 1); return structuredClone(goal);
      },
      updatePersonalGoal: async (goal, changes) => {
        const current = goals.find(item => item.id === goal.id)!;
        if (current.revision !== goal.revision) throw new Error("目标已变化");
        const scopeChanged = changes.constraints !== undefined || changes.non_goals !== undefined;
        if (scopeChanged && changes.scope_confirmed !== true) throw new Error("未确认范围");
        const updated: PersonalGoal = { ...current, ...changes, revision: current.revision + 1,
          ...(scopeChanged ? { scope_revision: current.revision + 1, progress: { ...current.progress, passed: 0, failed: 0 },
            acceptance_criteria: current.acceptance_criteria.map(item => ({ ...item, status: "pending" })),
            evidence: current.evidence.map(item => ({ ...item, stale_reason: "目标约束或非目标已更新，需按新范围复验" })) } : {}) };
        goals = goals.map(item => item.id === goal.id ? updated : item); setWrites(n => n + 1); return structuredClone(updated);
      },
      confirmPersonalGoal: unsupported, configurePersonalGoalFollowUp: unsupported, acknowledgePersonalGoalFollowUp: unsupported,
    } satisfies PersonalGoalClient;
  }, []);
  return <main className="vc-panel" style={{ width: "min(100%, var(--vc-panel-w, 352px))", height: "100vh", overflowY: "auto", padding: "var(--vc-space-3)", boxSizing: "border-box", background: "var(--vc-bg)", color: "var(--vc-text)" }}>
    <p>离线夹具 · 保存次数 <output>{writes}</output></p>
    <button onClick={() => setConnected(value => !value)}>{connected ? "模拟断线" : "恢复连接"}</button>
    <AssistantGoals client={client} connected={connected} />
  </main>;
}

createRoot(document.getElementById("root")!).render(<Harness />);
