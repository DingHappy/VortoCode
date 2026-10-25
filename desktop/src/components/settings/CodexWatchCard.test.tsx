import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { CodexObserverStatus } from "./CodexWatchCard";
import type { CodexObserverRead } from "../../lib/codexObserverHealth";

const now = 1_000_000;
const value: CodexObserverRead = { status: "current", checkedAt: now, health: { schema_version: 1,
  configured: true, run_id: "loop-1", phase: "superseded", updated_at: now / 1000,
  last_observed_at: now / 1000, last_acknowledged_at: now / 1000, pending: true,
  transport_mode: "ordered", error_code: "source_replaced" } };
it("shows replacement and explicit confirmation time without offering automatic takeover", () => {
  const html = renderToStaticMarkup(<CodexObserverStatus value={value} busy={false} now={now} onRefresh={() => {}} />);
  expect(html).toContain("已被接替"); expect(html).toContain("不会自动抢回");
  expect(html).toContain("最近确认送达"); expect(html).toContain("刷新状态");
  expect(html).not.toContain("立即重启");
});
it("unread status is explicitly unknown and a pending read disables refresh", () => {
  const html = renderToStaticMarkup(<CodexObserverStatus value={{ status: "unavailable" }} busy now={now} onRefresh={() => {}} />);
  expect(html).toContain("状态待核对"); expect(html).toContain('disabled=""');
  expect(html).not.toContain("上报已确认");
});
