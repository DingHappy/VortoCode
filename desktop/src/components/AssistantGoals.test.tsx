import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { AssistantGoals, GoalFollowUp, GoalOperationNotice, PersonalGoalCard } from "./AssistantGoals";
import type { PersonalGoal } from "../types";
import type { PersonalGoalClient } from "../lib/personalGoals";
import { readPersonalGoalSnapshot } from "../lib/personalGoalSnapshot";

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
    evidence: [{ id: "proof-1", criterion_id: "criterion-1", kind: "personal_acceptance", source: "user_confirmation", summary: "已核对预算", passed: true }] };
  const html = renderToStaticMarkup(<PersonalGoalCard goal={achieved} disabled={false} onSave={async () => {}} onConfirm={async () => {}} />);
  expect(html).toContain("已达成"); expect(html).toContain("已核对预算");
  expect(html).toContain("记录未通过"); expect(html).toContain('<fieldset disabled=""');
});

it("shows complete historical acceptance as pending recheck while goal writes are disabled", () => {
  const achieved: PersonalGoal = { ...goal, status: "achieved", progress: { passed: 1, failed: 0, total: 1 },
    acceptance_criteria: [{ ...goal.acceptance_criteria[0], status: "passed", evidence_ids: ["proof-1"] }],
    evidence: [{ id: "proof-1", criterion_id: "criterion-1", kind: "personal_acceptance", source: "user_confirmation", summary: "已核对预算", passed: true }] };
  const html = renderToStaticMarkup(<PersonalGoalCard goal={achieved} disabled onSave={async () => {}} onConfirm={async () => {}} />);
  expect(html).toContain("达成记录待核对");
  expect(html).toContain("1/1 条通过记录待核对");
  expect(html).toContain("确认预算 · 通过记录待核对");
  expect(html).toContain("已核对预算");
  expect(html).not.toContain("已达成");
  expect(html).not.toContain("条已核实达标");
});

it("offline form stays disabled and says reminders require separate approval", () => {
  const html = renderToStaticMarkup(<AssistantGoals client={null} connected={false} />);
  expect(html).toContain("到点提醒需要单独批准");
  expect(html).toContain("连接后可读取和修改目标");
  expect(html).toContain('<fieldset disabled=""');
  expect(html).not.toContain("还没有个人目标");
});

it("retains the exact goal navigation intent while offline instead of showing an empty list", () => {
  const html = renderToStaticMarkup(<AssistantGoals client={null} connected={false} focusId="personal-target" onClearFocus={() => {}} />);
  expect(html).toContain("连接后核对指定目标");
  expect(html).toContain("查看全部目标");
  expect(html).not.toContain("还没有个人目标");
});

it("requires a current connection read before enabling goal forms", () => {
  const client = { listPersonalGoals: async () => [goal] } as PersonalGoalClient;
  const html = renderToStaticMarkup(<AssistantGoals client={client} connected focusId={goal.id} />);
  expect(html).toContain("正在核对目标，暂不能修改");
  expect(html).toContain("正在核对指定目标");
  expect(html).toContain('<fieldset disabled=""');
  expect(html).toContain('<button disabled="">正在核对…</button>');
  expect(html).not.toContain("已定位指定目标");
  expect(html).not.toContain("还没有个人目标");
});

it("a focused personal goal has a stable navigation anchor without submitting acceptance", () => {
  const html = renderToStaticMarkup(<PersonalGoalCard goal={goal} focused disabled={false} onSave={async () => {}} onConfirm={async () => {}} />);
  expect(html).toContain('data-personal-goal-id="personal-example"');
  expect(html).toContain('tabindex="-1"');
  expect(html).toContain("待核对");
});

it("distinguishes waiting for a write from an accepted write awaiting read-back", () => {
  const submitting = renderToStaticMarkup(<GoalOperationNotice operation={{ phase: "submitting", goalId: goal.id }} />);
  expect(submitting).toContain("正在等待提交结果");
  expect(submitting).toContain("离开页面不代表提交已取消");
  expect(submitting).not.toContain("上次操作已提交");
  const accepted = renderToStaticMarkup(<GoalOperationNotice operation={{ phase: "accepted", goalId: goal.id }} />);
  expect(accepted).toContain("上次操作已提交");
  expect(accepted).toContain("核对版本后");
});

it.each([false, true])("keeps a missing-receipt notice honest after checked=%s", checked => {
  const html = renderToStaticMarkup(<GoalOperationNotice operation={{ phase: "unconfirmed", goalId: goal.id, checked }} />);
  expect(html).toContain("未取得成功回执");
  expect(html).toContain(checked ? "已读取当前目标" : "请先刷新当前目标");
  expect(html).not.toContain("上次操作已提交");
  expect(html).not.toContain("<button");
});

it("has no residual operation notice after a verified success", () => {
  expect(renderToStaticMarkup(<GoalOperationNotice operation={{ phase: "idle" }} goal={goal} onInspect={() => {}} />)).toBe("");
});

it("offers read-only navigation to the exact submitted goal without invoking it during render", () => {
  const inspect = vi.fn();
  const html = renderToStaticMarkup(<GoalOperationNotice operation={{ phase: "unconfirmed", goalId: goal.id, checked: true }} goal={goal} onInspect={inspect} />);
  expect(html).toContain(`上次操作目标：<strong>${goal.objective}</strong>`);
  expect(html).toContain(`aria-label="核对上次操作目标：${goal.objective}"`);
  expect(html).toContain("核对这条目标");
  expect(html).not.toContain("重新提交");
  expect(inspect).not.toHaveBeenCalled();
});

it("does not label another goal as the submitted target and disables navigation offline", () => {
  const html = renderToStaticMarkup(<GoalOperationNotice operation={{ phase: "accepted", goalId: "personal-missing" }}
    goal={goal} disabled onInspect={() => {}} />);
  expect(html).toContain("尚未读取到对应记录");
  expect(html).not.toContain(goal.objective);
  expect(html).toContain('<button disabled=""');
});

it("does not display stored achievement or criterion counts as verified without matching evidence", () => {
  const unproven: PersonalGoal = { ...goal, status: "achieved", progress: { passed: 1, failed: 0, total: 1 },
    acceptance_criteria: [{ ...goal.acceptance_criteria[0], status: "passed", evidence_ids: ["missing"] }] };
  const html = renderToStaticMarkup(<PersonalGoalCard goal={unproven} disabled={false} onSave={async () => {}} onConfirm={async () => {}} />);
  expect(html).toContain("达成记录待核对");
  expect(html).toContain("通过记录待核对");
  expect(html).toContain("0/1 条已核实达标");
  expect(html).not.toContain("已达成");
});

it.each(["goal", "criterion"])("an unknown %s state is displayed conservatively with mutations disabled", level => {
  const future = { ...goal, status: level === "goal" ? "future" : goal.status,
    acceptance_criteria: goal.acceptance_criteria.map(item => ({ ...item, status: level === "criterion" ? "future" : item.status })) };
  const html = renderToStaticMarkup(<PersonalGoalCard goal={future} disabled={false} onSave={async () => {}} onConfirm={async () => {}} />);
  expect(html).toContain("状态待核对");
  expect(html).toContain('<fieldset disabled=""');
  expect(html).toContain('<textarea disabled=""');
  expect(html).not.toContain("推进中");
});

const remindable: PersonalGoal = { ...goal, follow_up_at: "2026-10-18T10:00:00Z", follow_up_runtime_configured: true,
  follow_up_preview: "📌 目标跟进提醒\n核对旅行日期" };
const renderReminder = (item: PersonalGoal, dirty = false) => renderToStaticMarkup(
  <GoalFollowUp goal={item} disabled={false} dirty={dirty} onChange={async () => {}} onAcknowledge={async () => {}} />);

it("does not interpret an unknown reminder state as permission to schedule another notification", () => {
  const [future] = readPersonalGoalSnapshot([{ ...remindable, follow_up: { status: "future", attempts: 1 } }]);
  const html = renderReminder(future);
  expect(html).toContain("提醒状态待核对");
  expect(html).not.toContain("提醒尚未批准");
  expect(html).toContain('<button disabled="">批准按时提醒</button>');
  expect(html).toContain('<button disabled="">我已看到这条提醒</button>');
});

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
