import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { StaffActionCard } from "./StaffActionCard";
import { TaskQuestionActions } from "./TaskQuestionActions";
import type { TaskItem } from "../types";
import type { StaffAction } from "../lib/staffAction";
function task(): TaskItem {
  return { id: "task-0123456789", kind: "delegation", status: "blocked", prompt: "整理目标", collaboration: {
    round: 1, assignee: "staff-helper", review: "not_submitted", acceptance: [], tainted: true, messages: [],
    dispatch: { source: "staff", max_steps: 12, timeout_seconds: 600 }, approval: { status: "pending", reason: "", decision: null,
      digest: "digest", payload: { id: "action", task_id: "task-0123456789", round: 1, member: "staff-helper", member_revision: 1,
        role: "organizer", role_revision: 1, instance: "instance", runtime: "runtime", nonce: "nonce", kind: "own_data", expires_at: 2000000000, tool: "personal_goal_create",
        message: "整理目标 <script>ignored()</script>", args: { objective: "完整正文" }, tainted: true, approval_level: "normal" } } as StaffAction } };
}
const getClient = () => null, onUpdated = () => {};
it("shows the action, complete arguments, owner decision controls and external-content warning", () => {
  const html = renderToStaticMarkup(<StaffActionCard task={task()} getClient={getClient} onUpdated={onUpdated} />);
  for (const text of ["专员等你批准这一步", "staff-helper", "organizer", "完整正文", "本轮读过外部内容", "拒绝理由", "拒绝这一步", "批准这一步"])
    expect(html).toContain(text);
  expect(html).toContain("&lt;script&gt;"); expect(html).not.toContain("<script>");
  expect(html.match(/<button[^>]*>批准这一步<\/button>/)?.[0]).toContain("disabled");
});
it("does not also render the unrelated question form for an action approval", () => {
  expect(renderToStaticMarkup(<TaskQuestionActions task={task()} getClient={getClient} onUpdated={onUpdated} />)).toBe("");
});
it("keeps interrupted cards as history without an approval button", () => {
  const value = task(); value.status = "interrupted"; value.collaboration!.approval!.status = "interrupted";
  const html = renderToStaticMarkup(<StaffActionCard task={value} getClient={getClient} onUpdated={onUpdated} />);
  expect(html).toContain("旧卡不能继续执行"); expect(html).not.toContain("<button");
});
