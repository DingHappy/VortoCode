import { expect, it } from "vitest";
import { roleProposalDraft } from "./NewRoleDialog";
import type { StaffRole } from "../lib/staff";

it("prepares a request referring to the approved role revision, without serializing a permission grant", () => {
  const role: StaffRole = { id: "calendar-reader", title: "日程只读", brief: "登记方案", builtin: true, enabled: true, revision: 4,
    grants: { operations: ["read"], tools: ["calendar_events"], sees: ["plugin:calendar"], web: false, project: false },
    engine: ["vortocode"], max_steps: 12, timeout_seconds: 600 };
  const text = roleProposalDraft(" 日程助手 ", " 整理今日安排 ", role);
  expect(text).toContain("日程助手");
  expect(text).toContain("calendar-reader，修订 4");
  expect(text).toContain("由我核对后确认");
  expect(text).toContain("现在不要派发任务");
  expect(text).not.toContain("plugin:calendar");
  expect(text).not.toContain("calendar_events");
});

it("requests a minimal proposal when no registered permission template was selected", () => {
  expect(roleProposalDraft("研究助手", "核对公开来源")).toContain("请先提议满足这项分工的最小权限方案");
});
