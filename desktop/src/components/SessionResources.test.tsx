import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { SessionResources } from "./SessionResources";
import { emptyResourceView, type ResourceView } from "../lib/sessionResources";

const snapshot = { version: 1 as const, session: "sid-one", nonce: "n1", observed_at: "2026-11-01T12:00:00Z",
  status: "observed" as const, truncated: false, attention_id: "c1", items: [{ id: "browser", kind: "browser" as const,
    title: "网站浏览器", state: "needs_human" as const, approval: "each_action" as const, target: "https://example.com",
    account: "工作", device: "", checked_at: null, detail: "仅会话记录" }] };
function render(view: Partial<ResourceView> = {}, connected = true, pendingId = "c1") {
  return renderToStaticMarkup(<SessionResources view={{ ...emptyResourceView(), snapshot, ...view }} connected={connected}
    hostLabel="常驻运行端" pendingId={pendingId} onRefresh={() => {}} onFocusConfirmation={() => {}} onManage={() => {}} />);
}
it("separates observed sessions, action approval and actual accounts", () => {
  const html = render({ stale: false });
  expect(html).toContain("等待人工处理"); expect(html).toContain("交互动作每次确认");
  expect(html).toContain("实际账号待核对"); expect(html).toContain("不代表已登录、已授权执行或任务完成");
  expect(html).toContain("查看当前确认"); expect(render({}, true, "other")).not.toContain("查看当前确认");
});
it("retains an explicitly stale observation offline and disables refresh", () => {
  const html = render({}, false);
  expect(html).toContain("连接中断"); expect(html).toContain("保留上次记录"); expect(html).toContain('disabled=""');
  expect(html).toContain("上次记录：");
});
it("does not hide incomplete or missing inventory and does not infer tool locations", () => {
  expect(render({ snapshot: { ...snapshot, truncated: true } })).toContain("未列出的能力保持未知");
  expect(render({ snapshot: { ...snapshot, status: "not_loaded", items: [] } })).toContain("会话尚未初始化");
  const service = { ...snapshot.items[0], id: "mcp", kind: "service" as const };
  expect(render({ snapshot: { ...snapshot, items: [service] } })).toContain("对应 MCP 服务端，实际位置待核对");
});
it("renders account labels as escaped text", () => {
  const html = render({ snapshot: { ...snapshot, items: [{ ...snapshot.items[0], account: "<img src=x onerror=alert(1)>" }] } });
  expect(html).toContain("&lt;img"); expect(html).not.toContain("<img");
});

it("keeps unopened inventory neutral and highlights actual handoff or uncertain actions", () => {
  const empty = render({ snapshot: null, stale: true });
  expect(empty).toContain("常驻运行端 · 连接与能力");
  expect(empty).toContain("按需查看");
  expect(empty).not.toContain("<small>待核对</small>");
  expect(render({ stale: false })).toContain("<small>需要核对</small>");
  expect(empty).not.toContain('<details class="session-resources" open');
});
