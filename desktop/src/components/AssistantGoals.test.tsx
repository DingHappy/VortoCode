import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { AssistantGoals, PersonalGoalCard } from "./AssistantGoals";
import type { PersonalGoal } from "../types";

const goal: PersonalGoal = { id: "personal-example", kind: "personal", revision: 1, objective: "整理旅行计划",
  acceptance_criteria: [{ id: "criterion-1", text: "确认预算", status: "pending", evidence_ids: [] }],
  constraints: [], non_goals: [], status: "blocked", task_ids: [], evidence: [],
  next_action: "查询日期", blocker: "等待回复", due_at: "", follow_up_at: "", overdue: true, follow_up_due: true,
  progress: { passed: 0, failed: 0, total: 1 },
};

it("shows honest progress, followup flags, next step and explicit acceptance controls", () => {
  const html = renderToStaticMarkup(<PersonalGoalCard goal={goal} disabled={false} onSave={async () => {}} onConfirm={async () => {}} />);
  for (const text of ["有阻塞", "0/1", "已过期限", "到了跟进时间", "等待回复", "查询日期", "待核对", "验收依据", "确认达标", "记录未通过"]) expect(html).toContain(text);
  expect(html).not.toContain("开始执行");
});

it("keeps acceptance evidence visible after achievement and allows recording a failed recheck", () => {
  const achieved: PersonalGoal = { ...goal, status: "achieved", progress: { passed: 1, failed: 0, total: 1 },
    acceptance_criteria: [{ ...goal.acceptance_criteria[0], status: "passed", evidence_ids: ["proof-1"] }],
    evidence: [{ id: "proof-1", criterion_id: "criterion-1", kind: "personal_acceptance", summary: "已核对预算", passed: true }] };
  const html = renderToStaticMarkup(<PersonalGoalCard goal={achieved} disabled={false} onSave={async () => {}} onConfirm={async () => {}} />);
  expect(html).toContain("已达成"); expect(html).toContain("已核对预算");
  expect(html).toContain("记录未通过"); expect(html).toContain('<fieldset disabled=""');
});

it("offline form stays disabled and does not promise scheduled execution", () => {
  const html = renderToStaticMarkup(<AssistantGoals client={null} connected={false} />);
  expect(html).toContain("自动提醒尚未接入");
  expect(html).toContain("连接后可读取和修改目标");
  expect(html).toContain('<fieldset disabled=""');
  expect(html).not.toContain("还没有个人目标");
});
