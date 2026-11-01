import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { skillLearningDraft } from "./skillLearning";
import { PersonalSkillRequests } from "./personalSkills";
import { SkillLearningCard, SkillProposalReview } from "../components/SkillLearningCard";
import type { SkillLearningSource, SkillProposal } from "../types";

const source: SkillLearningSource = { dispatch_id: "dsp-source", title: "重连", goal: "旧连接不得回写", project: "fixture",
  revision: "source-version", criteria: ["不重放请求"], head: "a".repeat(40), checked_at: "2026-11-01T12:00:00Z", verified_at: "2026-11-01T12:00:00Z" };
function proposal(change: Partial<SkillProposal> = {}): SkillProposal {
  return { id: "proposal", skill_id: "reconnect", status: "pending", revision: "review-version", source,
    created_at: source.checked_at, draft: skillLearningDraft(source), target_revision: "b".repeat(64), current_revision: null, current_enabled: false,
    stale: false, source_stale: false, diff: "+检查旧连接", diff_redacted: false, proposed_by: "instance_operator", reviewed_by: null, reviewed_at: null, history: [], ...change };
}
function review(change: Partial<SkillProposal> = {}) {
  return renderToStaticMarkup(<SkillProposalReview proposal={proposal(change)} disabled={false} onDecide={() => {}} />);
}

describe("operator-reviewed learning", () => {
  it("starts a checklist with source criteria and explicit stop conditions", () => {
    const draft = skillLearningDraft(source);
    expect(draft.revision).toBeNull(); expect(draft.instructions).toContain("不重放请求");
    expect(draft.instructions).toContain("暂停并报告，不循环重试");
    expect(draft.instructions).not.toContain(source.dispatch_id);
    expect(draft.instructions).not.toContain(source.head);
  });
  it("preserves the inspected target version and original instructions", () => {
    const draft = skillLearningDraft(source, { id: "reconnect", name: "流程", description: "description", instructions: "原流程", revision: "exact-version" } as never);
    expect(draft.revision).toBe("exact-version"); expect(draft.instructions.startsWith("原流程\n\n")).toBe(true);
    expect(draft.name).toBe("流程");
  });
  it("shows provenance, full diff and a disabled-save decision rather than enabling", () => {
    const html = review();
    expect(html).toContain("dsp-source"); expect(html).toContain("技能版本差异");
    expect(html).toContain("已审阅差异，保存为停用版本"); expect(html).not.toContain("已审阅，启用此版本");
    expect(html).toContain("尚未审阅"); expect(html).toContain("本实例操作人");
  });
  it.each([{ stale: true }, { source_stale: true }])("blocks acceptance of a stale source or target %j", change => {
    const html = review(change);
    expect(html).toContain('disabled=""'); expect(html).toContain("不能应用此提案"); expect(html).toContain("拒绝提案");
  });
  it("offers explicit acknowledgement of a written version, without automatic retry", () => {
    const html = review({ status: "applying", current_revision: "b".repeat(64) });
    expect(html).toContain("不会自动重试"); expect(html).toContain("核对已写入版本");
    expect(html).not.toContain("已审阅差异，保存为停用版本");
    expect(review({ status: "applying" })).toContain('disabled=""');
  });
  it.each(["accepted", "rejected"] as const)("does not re-offer a decision after %s", status => {
    const html = review({ status });
    expect(html).not.toContain("已审阅差异，保存为停用版本"); expect(html).not.toContain(">拒绝提案<");
  });
  it("escapes source and diff text as content", () => {
    const html = review({ diff: "<script>fixture</script>", source: { ...source, title: "<img onerror=fixture>" } });
    expect(html).not.toContain("<script>"); expect(html).not.toContain("<img "); expect(html).toContain("&lt;script&gt;");
  });
  it("keeps creation disabled while disconnected", () => {
    const html = renderToStaticMarkup(<SkillLearningCard client={null} connection="disconnected" skills={[]} onSaved={async () => {}} />);
    expect(html).toContain("disabled"); expect(html).toContain("无自动学习、自动启用或后台重试");
  });
});

it("drops a late write response before React sees a same-client reconnect", async () => {
  const owner = { connectionEpoch: 1 }; const requests = new PersonalSkillRequests(); requests.bind(owner, true);
  let release!: () => void; const wait = new Promise<void>(done => { release = done; });
  const followup = vi.fn(); let stillCurrent!: () => boolean;
  const old = requests.run(async current => { stillCurrent = current; await wait; if (current()) followup(); });
  owner.connectionEpoch++; expect(stillCurrent()).toBe(false);
  release(); await old; expect(followup).not.toHaveBeenCalled();
  requests.bind(owner, true); await requests.run(async () => { followup(); });
  expect(followup).toHaveBeenCalledOnce();
});
