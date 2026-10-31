import { expect, it } from "vitest";
import { emptyPersonalGoalDraft, personalGoalDraft } from "./personalGoalDraft";

function submitted() {
  const draft = personalGoalDraft(emptyPersonalGoalDraft("empty"),
    { type: "edit", field: "objective", value: "准备行程", requestId: "sent" });
  return personalGoalDraft(draft, { type: "submit", requestId: "sent" });
}

it("keeps a retry's request identity until the submitted record is actually read", () => {
  const draft = submitted();
  expect(personalGoalDraft(draft, { type: "edit", field: "objective", value: "准备行程", requestId: "unused" })).toBe(draft);
  expect(personalGoalDraft(draft, { type: "read", goalIds: [], requestId: "unused" })).toBe(draft);
  expect(personalGoalDraft(draft, { type: "read", goalIds: ["personal-other"], requestId: "unused" })).toBe(draft);
  expect(personalGoalDraft(draft, { type: "submit", requestId: "older" })).toBe(draft);
});

it("clears all submitted fields once, including after a lost receipt or failed read-back", () => {
  const draft = { ...submitted(), criteria: "确认预算", next: "查日期", due: "2026-11-01T10:00", followUp: "2026-10-30T10:00" };
  const found = personalGoalDraft(draft, { type: "read", goalIds: ["personal-sent"], requestId: "fresh" });
  expect(found).toEqual({ ...emptyPersonalGoalDraft("fresh"), foundGoalId: "personal-sent" });
  expect(personalGoalDraft(found, { type: "read", goalIds: ["personal-sent"], requestId: "unused" })).toBe(found);
});

it.each(["objective", "criteria", "next", "due", "followUp", "constraints", "nonGoals"] as const)("preserves newer %s input when an old submitted record appears", field => {
  const edited = personalGoalDraft(submitted(), { type: "edit", field, value: "新内容", requestId: "new" });
  expect(personalGoalDraft(edited, { type: "read", goalIds: ["personal-sent"], requestId: "unused" })).toBe(edited);
  expect(edited.submittedRequestId).toBeNull();
  const reverted = personalGoalDraft(edited, { type: "edit", field, value: submitted()[field], requestId: "reverted" });
  expect(personalGoalDraft(reverted, { type: "read", goalIds: ["personal-sent"], requestId: "unused" })).toBe(reverted);
  expect(reverted.requestId).toBe("reverted");
});

it("never clears an unsubmitted draft even if its ID appears in a read", () => {
  const draft = emptyPersonalGoalDraft("new");
  expect(personalGoalDraft(draft, { type: "read", goalIds: ["personal-new"], requestId: "unused" })).toBe(draft);
});

it("drops the old submission association on client reset and on the next draft", () => {
  const reset = personalGoalDraft(submitted(), { type: "reset", requestId: "other-client" });
  expect(reset).toEqual(emptyPersonalGoalDraft("other-client"));
  expect(personalGoalDraft(reset, { type: "read", goalIds: ["personal-sent"], requestId: "unused" })).toBe(reset);
  const found = personalGoalDraft(submitted(), { type: "read", goalIds: ["personal-sent"], requestId: "fresh" });
  const edited = personalGoalDraft(found, { type: "edit", field: "objective", value: "下一件事", requestId: "next" });
  expect(edited.foundGoalId).toBeNull();
  expect(personalGoalDraft(edited, { type: "submit", requestId: "sent" })).toBe(edited);
});
