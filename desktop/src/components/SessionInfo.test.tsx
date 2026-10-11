import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { ContextStatus, SessionInfo } from "./SessionInfo";
import { contextStatusText } from "./ContextStatus";
import { matchingCommands } from "./ComposerCommands";
const context = { used_tokens: 6000, max_tokens: 8000, pct: 75, context_window_tokens: 1000000, context_window_pct: .6, history_messages: 24, policy: "preserve", will_compact: true, compact_enabled: true };
const props = { busy: true, connected: true, plan: [], activities: [], queued: 2, model: "本地模型", onRefresh() {} };
it("shows only the percentage and keeps details out of the sidebar until clicked", () => {
  const html = renderToStaticMarkup(<ContextStatus context={context} />);
  expect(html).toContain('>0.6%</button>'); expect(html).toContain('aria-expanded="false"');
  expect(html).not.toContain("6,000"); expect(html).not.toContain("待压缩");
  const sidebar = renderToStaticMarkup(<SessionInfo {...props} context={context} />);
  expect(sidebar).not.toContain("6,000"); expect(sidebar).not.toContain("压缩触发依据");
  expect(sidebar).toContain("会话详情");
  expect(sidebar).not.toContain("<select");
});
it("keeps model window and history budget distinct in popup details", () => {
  expect(contextStatusText(context)).toMatchObject({ percent: "0.6%", detail: "6,000 / 1,000,000 tokens", compression: "下一回合开始前尝试压缩" });
  expect(contextStatusText({ ...context, context_window_tokens: 0 })).toMatchObject({ percent: "75%", label: "历史预算使用量（模型窗口未知）", detail: "6,000 / 8,000 tokens · 历史预算" });
  expect(contextStatusText({ ...context, compact_enabled: false }).compression).toBe("自动压缩已关闭");
  expect(contextStatusText({ ...context, compact_enabled: null }).compression).toBe("压缩状态未知");
  expect(contextStatusText().percent).toBe("—");
});
it("slash matches real entry points and hides repository references from general sessions", () => {
  expect(matchingCommands("/files", false)).toEqual([]);
  expect(matchingCommands("/files", true).map(item => item.id)).toEqual(["files"]);
  expect(matchingCommands("/模型", false).map(item => item.id)).toEqual(["model"]);
  expect(matchingCommands("/compact", true)).toEqual([]);
});

it("keeps an idle session focused and details collapsed", () => {
  const html = renderToStaticMarkup(<SessionInfo {...props} busy={false} queued={0} />);
  expect(html).toContain("发送消息开始"); expect(html).not.toContain("执行进度");
  expect(html).not.toContain("最近活动"); expect(html).not.toContain("排队中");
  expect(html).toContain('<details class="session-info-details">'); expect(html).not.toContain(' open=');
});
it("keeps confirmation and failure information ahead of secondary settings", () => {
  const waiting = renderToStaticMarkup(<SessionInfo {...props} needsInput activities={[{ id: "error", kind: "tool", status: "failed", label: "搜索网页" }]} />);
  expect(waiting).toContain("等待确认"); expect(waiting).toContain("输入框上方确认"); expect(waiting).toContain("失败");
  expect(waiting.indexOf("搜索网页")).toBeLessThan(waiting.indexOf("会话详情"));
  const offline = renderToStaticMarkup(<SessionInfo {...props} connected={false} onOpenSettings={() => {}} />);
  expect(offline).toContain("连接中断"); expect(offline).toContain("打开连接设置");
});
it("keeps only conversation execution progress in the sidebar",()=>{
  const html=renderToStaticMarkup(<SessionInfo {...props} plan={[{step:"读取来源",status:"completed"},{step:"核对结论",status:"in_progress"},{step:"交付",status:"pending"}]} activities={[{id:"t1",kind:"tool",status:"running",label:"读取网页"}]} />);
  for(const text of ["执行进度","1/3","核对结论"])expect(html).toContain(text);
  for(const text of ["本会话下派任务","看板","Goal","查看编排图"])expect(html).not.toContain(text);
});
it("does not invent a plan when the runtime has not provided one",()=>{
  const html=renderToStaticMarkup(<SessionInfo {...props}/>);
  expect(html).not.toContain("执行进度");expect(html).not.toContain("下派状态");
});
