import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { AssistantGoals, GoalFollowUp, PersonalGoalCard } from "./AssistantGoals";
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

it("offline form stays disabled and says reminders require separate approval", () => {
  const html = renderToStaticMarkup(<AssistantGoals client={null} connected={false} />);
  expect(html).toContain("到点提醒需要单独批准");
  expect(html).toContain("连接后可读取和修改目标");
  expect(html).toContain('<fieldset disabled=""');
  expect(html).not.toContain("还没有个人目标");
});

const remindable: PersonalGoal = { ...goal, follow_up_at: "2026-10-18T10:00:00Z", follow_up_runtime_configured: true,
  follow_up_preview: "📌 目标跟进提醒\n核对旅行日期" };
const renderReminder = (item: PersonalGoal, dirty = false) => renderToStaticMarkup(
  <GoalFollowUp goal={item} disabled={false} dirty={dirty} onChange={async () => {}} onAcknowledge={async () => {}} />);

it("shows the exact body and requires saving edited progress before approval", () => {
  const ready = renderReminder(remindable);
  expect(ready).toContain("核对旅行日期");
  expect(ready).toContain("将发送给已配对通知渠道的正文");
  expect(ready).toContain("<button>批准按时提醒</button>");
  const dirty = renderReminder(remindable, true);
  expect(dirty).toContain("请先保存进展");
  expect(dirty).toContain('<button disabled="">批准按时提醒</button>');
});

it("does not offer approval on unconfigured, undated or achieved goals", () => {
  for (const item of [{ ...remindable, follow_up_runtime_configured: false }, { ...remindable, follow_up_at: "" }, { ...remindable, status: "achieved" }]) {
    expect(renderReminder(item)).toContain('<button disabled="">批准按时提醒</button>');
  }
});

it("offers pause on scheduled reminders and distinguishes acceptance from a user receipt", () => {
  const pending = renderReminder({ ...remindable, follow_up: { status: "scheduled", attempts: 0 } });
  expect(pending).toContain("暂停提醒"); expect(pending).not.toContain("我已看到这条提醒");
  const accepted = renderReminder({ ...remindable, follow_up: { status: "accepted", attempts: 1 } });
  expect(accepted).toContain("通知已受理，未确认送达或已读");
  expect(accepted).toContain("我已看到这条提醒");
  const receipt = renderReminder({ ...remindable, follow_up: { status: "acknowledged", attempts: 1 } });
  expect(receipt).toContain("用户已确认看到提醒"); expect(receipt).not.toContain("我已看到这条提醒");
});

it("requires acknowledging uncertainty before reapproval and retains old outcomes", () => {
  const html = renderReminder({ ...remindable, follow_up: { status: "unknown", attempts: 1 },
    follow_up_history: [{ id: "old", status: "accepted", attempts: 1 }] });
  expect(html).toContain("接受重新批准可能产生重复提醒");
  expect(html).toContain('<button disabled="">核对后重新批准</button>');
  expect(html).toContain("以前的提醒记录");
});
