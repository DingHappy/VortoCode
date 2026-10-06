import { describe, expect, it } from "vitest";
import type { TaskItem, TaskQuestion } from "../types";
import { taskQuestionIdentity } from "./taskQuestion";

const now = Date.now();
const question: TaskQuestion = { id: "question-first", task_id: "task-research", round: 1, status: "open",
  question: "检查哪个环境？", options: ["测试", "生产"], context: "已定位配置", asked_by: "reader", answerer: "owner",
  created: new Date(now).toISOString(), expires_at: new Date(now + 86400000).toISOString(), answer: "", answered_at: "" };
function task(q = question): TaskItem {
  return { id: "task-research", kind: "delegation", status: "blocked", owner_session: "sid-owner",
    collaboration: { assignee: "reader", round: 1, review: "not_submitted", acceptance: [], tainted: true, messages: [],
      dispatch: { source: "api", max_steps: 4, timeout_seconds: 30 }, questions: [q] } };
}

describe("task question actions", () => {
  it("binds the owner's exact open question and round", () => {
    expect(taskQuestionIdentity(task(), now)).toEqual({ session: "owner", round: 1, question });
  });
  it.each([
    { status: "answered" }, { status: "expired" }, { status: "cancelled" }, { task_id: "task-other" },
    { round: 2 }, { id: "question-bad/../path" }, { id: "question-first\n" }, { question: " " },
    { expires_at: "broken" }, { expires_at: new Date(now).toISOString() }, { options: ["a", "b", "c", "d"] },
  ] as Partial<TaskQuestion>[])("rejects unavailable or mismatched questions %j", (change) => {
    expect(taskQuestionIdentity(task({ ...question, ...change }), now)).toBeNull();
  });
  it("requires a blocked service task with exactly one open question", () => {
    const item = task();
    item.status = "running";
    expect(taskQuestionIdentity(item, now)).toBeNull();
    item.status = "blocked";
    item.collaboration!.questions = [question, { ...question, id: "question-other" }];
    expect(taskQuestionIdentity(item, now)).toBeNull();
    item.collaboration!.questions = [question];
    delete item.collaboration!.dispatch;
    expect(taskQuestionIdentity(item, now)).toBeNull();
  });

  it("binds a development question to its exact plan block and owner", () => {
    const q = { ...question, task_id: "task-dev", plan_id: "plan-dev", block_id: "todo", plan_revision: "a".repeat(64), branch: "vorto/dev" };
    const item: TaskItem = { id: "task-dev", kind: "dev-resume", status: "blocked", owner_session: "sid-owner", plan_id: "plan-dev",
      development: { version: 1, round: 1, owner_session: "sid-owner", plan_id: "plan-dev", root_task_id: "task-root", questions: [q] } };
    expect(taskQuestionIdentity(item, now)).toEqual({ session: "owner", round: 1, question: q, development: true });
    item.development!.questions = [{ ...q, plan_id: "other" }];
    expect(taskQuestionIdentity(item, now)).toBeNull();
    item.development!.questions = [{ ...q, plan_revision: "broken" }];
    expect(taskQuestionIdentity(item, now)).toBeNull();
    item.development!.questions = [q];
    item.development!.owner_session = "sid-other";
    expect(taskQuestionIdentity(item, now)).toBeNull();
  });
});
