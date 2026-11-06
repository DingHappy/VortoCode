import { expect, it } from "vitest";
import { assistantAttention } from "./assistantAttention";
import { taskFeedItem } from "./activityFeed";
import { teamTaskContext } from "./teamAttention";
import type { TeamSummary } from "../types";

const team: TeamSummary = { id: "team", objective: "日报", status: "review", mode: "open", used: {}, limits: {}, members: [],
  children: [{ id: "member", member: "研究员", status: "done", tainted: true }], tainted: true, waiting_reason: "" };
const member = taskFeedItem({ id: "member", kind: "delegation", status: "done", result: "成员原文", collaboration: {
  assignee: "研究员", round: 1, review: "pending", acceptance: [], tainted: true, messages: [],
} });

it("keeps known team reviews and failures in history without asking the owner to manage internal rounds", () => {
  const failed = { ...member, id: "failure", status: "failed" as const };
  const root = taskFeedItem({ id: team.id, kind: "team", status: "review" });
  const view = assistantAttention([member, failed, root], [team]);
  expect(view.decisions).toEqual([]); expect(view.exceptions).toEqual([]);
  expect(view.teamRecords).toHaveLength(2); expect(view.history).toHaveLength(3);
  expect(view.history[0].detail).toBe("成员原文");
  expect(view.history[0].statusLabel).toBe("小组内部待审阅");
  expect(member.teamContext).toBeUndefined(); expect(member.summary).toBe("已交付，等你验收");
});

it("preserves explicit questions, unknown membership, and ambiguous membership as owner-visible records", () => {
  const question = { ...member, requiresReply: true };
  expect(assistantAttention([question], [team]).decisions).toHaveLength(1);
  expect(assistantAttention([member], []).decisions).toEqual([member]);
  expect(assistantAttention(assistantAttention([member], [team]).history, []).decisions).toHaveLength(1);
  expect(assistantAttention([member], [team, { ...team, id: "another" }]).decisions).toEqual([member]);
  expect(teamTaskContext("unrelated", [team])).toBeUndefined();
});

it("leaves failures of the whole team visible and maps team lifecycle states accurately", () => {
  const failed = taskFeedItem({ id: team.id, kind: "team", status: "failed" });
  expect(assistantAttention([failed], [team]).exceptions).toEqual([failed]);
  expect(taskFeedItem({ id: team.id, kind: "team", status: "active" }).status).toBe("running");
  expect(taskFeedItem({ id: team.id, kind: "team", status: "waiting" }).status).toBe("waiting");
});
