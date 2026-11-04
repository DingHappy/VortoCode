export const snapshotFixture = () => ({ version: 1 as const, revision: "a".repeat(64), exported_at: "2026-11-03T12:00:00Z",
  scope: "当前常驻实例的长期记忆", instance_id: "未知", total: 1, count: 1, limit: 1000, coverage_limited: false,
  items: [{ id: "m1", content: "先给结论", recorded_at: "未知", source: "未知", confirmed_by: "未知", tainted: false, redacted: false }],
  markdown: "# 长期记忆快照\n先给结论" });
