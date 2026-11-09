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
  // 要核对时直接写是哪一项、什么情况，并用「等你」的颜色
  expect(render({ stale: false })).toContain('<small class="attention">网站浏览器等待人工处理</small>');
  expect(empty).not.toContain('<details class="session-resources" open');
});

it("助理模式下一切正常时不占位置，需要核对或断线时才出现；常驻电脑不写成「常驻电脑（常驻）」", async () => {
  const { residentHostLabel } = await import("./SessionResources");
  const calm = { ...snapshot, items: [{ ...snapshot.items[0], state: "loaded" as const }] };
  const quiet = (view: Partial<ResourceView>, connected = true) => renderToStaticMarkup(<SessionResources view={{ ...emptyResourceView(), ...view }}
    connected={connected} hostLabel="常驻电脑" onRefresh={() => {}} onFocusConfirmation={() => {}} onManage={() => {}} quietWhenOk />);
  expect(quiet({ snapshot: calm })).toBe("");
  expect(quiet({ snapshot })).toContain("网站浏览器等待人工处理");
  expect(quiet({ snapshot: calm }, false)).toContain("连接中断");
  expect(residentHostLabel("常驻电脑", true, true)).toBe("常驻电脑");
  expect(residentHostLabel("家里的 NAS", true, true)).toBe("家里的 NAS（常驻）");
  expect(residentHostLabel("实验室", true, false)).toBe("实验室（远端）");
  expect(residentHostLabel(undefined, false, false)).toBe("本机运行端");
});

it("折叠行说清楚要核对的是哪一项，多项时写另有几项", async () => {
  const { resourceSummary } = await import("./SessionResources");
  const view = (patch: Partial<ResourceView>) => ({ ...emptyResourceView(), snapshot, stale: false, ...patch });
  const second = { ...snapshot.items[0], id: "android", kind: "device" as const, title: "Android 手机", state: "needs_reconciliation" as const };
  expect(resourceSummary(view({}), true)).toEqual({ text: "网站浏览器等待人工处理", attention: true });
  expect(resourceSummary(view({ snapshot: { ...snapshot, items: [snapshot.items[0], second] } }), true).text)
    .toBe("网站浏览器等待人工处理，另有 1 项");
  expect(resourceSummary(view({}), false)).toEqual({ text: "连接中断", attention: true });
  expect(resourceSummary(view({ error: "超时" }), true)).toEqual({ text: "状态没读到", attention: true });
  const calm = { ...snapshot, items: [{ ...snapshot.items[0], state: "loaded" as const }] };
  expect(resourceSummary(view({ snapshot: calm }), true)).toEqual({ text: "查看详情", attention: false });
  expect(resourceSummary(view({ snapshot: null }), true)).toEqual({ text: "按需查看", attention: false });
});
