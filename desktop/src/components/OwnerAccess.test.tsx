import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { OwnerAccessDetails } from "./OwnerAccess";
import { ownerAccessSnapshot, approvalOutcome, type OwnerAccessSnapshot } from "../lib/ownerAccess";

const value = (): OwnerAccessSnapshot => ({ schema_version: 1, generated_at: "2026-11-03T10:00:00Z",
  permissions: { profile: "owner_device", effective_level: "reads", scopes: ["once"], taint_revokes_preauthorization: true },
  history: { status: "current", bounded: true, entries: [{ id: "h1", ts: "today", decision: "denied", tainted: true,
    operation_kind: "deliver", approval_outcome: "expired", decision_source: "timeout", approval_scope: "once", record_type: "card", origin: "app" }] } });

it("shows bounded approval metadata and does not equate approval with execution", () => {
  const html = renderToStaticMarkup(<OwnerAccessDetails value={ownerAccessSnapshot(value())} />);
  for (const text of ["对外发送", "已过期，未放行", "超时", "仅本次", "卡片处理", "已有授权失效", "批准不代表执行完成"]) expect(html).toContain(text);
  expect(html).not.toContain("已拒绝");
  expect(html).not.toContain("<button");
});

it("unavailable empty history stays visibly unverified", () => {
  const data = value(); data.history = { status: "unavailable", bounded: true, entries: [] };
  const html = renderToStaticMarkup(<OwnerAccessDetails value={data} />);
  expect(html).toContain("来源待核对"); expect(html).toContain("暂时无法确认审批记录");
  expect(html).not.toContain("没有审批记录");
});

it("rejects incompatible permission contracts and never guesses future outcomes", () => {
  for (const patch of [{ schema_version: 2 }, { permissions: { ...value().permissions, scopes: ["always"] } },
    { permissions: { ...value().permissions, effective_level: "full" } },
    { permissions: { ...value().permissions, taint_revokes_preauthorization: false } }]) {
    expect(() => ownerAccessSnapshot({ ...value(), ...patch })).toThrow();
  }
  expect(approvalOutcome("future", "allowed")).toBe("结果待核对");
  expect(approvalOutcome(undefined, "allowed")).toBe("已允许");
});

it("shows domain approval separately from uncertain delivery and automated review", () => {
  const data = value();
  data.history.entries = [{ id: "domain", ts: "today", record_type: "codex_action", domain_kind: "message", origin: "im",
    decision: "allowed", tainted: false, approval_outcome: "approved", approval_scope: "once", decision_source: "user", source_status: "unknown" },
    { id: "auto", ts: "today", record_type: "dispatch", origin: "runtime", decision: "allowed", tainted: false,
      approval_outcome: "approved", decision_source: "automatic_review", approval_scope: "none", source_status: "done" }];
  const html = renderToStaticMarkup(<OwnerAccessDetails value={data} />);
  for (const text of ["Codex 消息", "已允许", "IM", "发送结果无法核对", "后台审核", "运行端报告结束，验收仍需核对"]) expect(html).toContain(text);
  expect(html).not.toContain("已完成");
});

it("a legacy sent record is not presented as a known human approval", () => {
  const data = value(); data.history.status = "partial"; data.history.sources = { audit: "current", codex: "current", dispatches: "unavailable" };
  data.history.entries = [{ id: "old", ts: "", record_type: "codex_action", domain_kind: "interrupt", origin: "unknown",
    decision: "unknown", tainted: false, approval_outcome: "unknown", decision_source: "unknown", source_status: "sent", decision_metadata_missing: true }];
  const html = renderToStaticMarkup(<OwnerAccessDetails value={data} />);
  for (const text of ["结果待核对", "入口待核对", "审批时点待核对", "Codex 已受理，执行结果仍需核对", "待核对来源：", "任务单"]) expect(html).toContain(text);
  expect(html).not.toContain("已允许"); expect(html).not.toContain("已拒绝");
});
