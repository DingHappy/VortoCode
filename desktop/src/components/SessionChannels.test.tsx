import { expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { AssistantSidebar } from "./AssistantMode";
import { ChannelWelcome, CreateChannelDialog, SidebarChannels } from "./SessionChannels";
import { settingsTargetLease } from "../lib/settingsTarget";
import { desktopContext } from "../lib/desktopContext";
import type { SessionSummary } from "../types";

const channels: SessionSummary[] = [{ sid: "empty", title: "资料研究", messages: 0, updated: 1, channel: { version: 1, topic: "公开来源" } }];
it("lists empty channels, active state, topic search and explicit deletion separately from ordinary chats", () => {
  const props = { channels, current: "empty", onSelect: vi.fn(), onCreate: vi.fn(), onDelete: vi.fn() };
  const html = renderToStaticMarkup(<SidebarChannels {...props} query="来源" />);
  expect(html).toContain("资料研究"); expect(html).toContain('aria-current="page"'); expect(html).toContain("删除频道 资料研究");
  const ordinary = renderToStaticMarkup(<SidebarChannels {...props} channels={[{ ...channels[0], channel: undefined }]} />);
  expect(ordinary).not.toContain("删除频道 资料研究");
  const sidebar = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "main" }} sideChats={[]} {...props}
    query="来源" onNewSideChat={vi.fn()} onNewChannel={vi.fn()} />);
  expect(sidebar).toContain("资料研究"); expect(sidebar).not.toContain("未找到匹配的会话");
  const offline = renderToStaticMarkup(<SidebarChannels {...props} disabled channels={[{ ...channels[0], status: "working" }]} />);
  expect(offline).not.toContain('aria-label="进行中"');
});

it("renders a channel topic as text, not markup or a tool grant", () => {
  const html = renderToStaticMarkup(<ChannelWelcome title="研究" topic="<script>grant()</script>" />);
  expect(html).toContain("&lt;script&gt;"); expect(html).not.toContain("<script>");
});

it("keeps unavailable-space creation disabled and identifies the reviewed target", () => {
  const context = desktopContext({ registrationId: null, assistantId: null, scope: "general", repoRoot: "", sid: "main",
    baseUrl: "http://127.0.0.1:8080", runtimeId: "", connection: "disconnected", profiles: [], localResidentId: null, source: null });
  const createChannel = vi.fn();
  const html = renderToStaticMarkup(<CreateChannelDialog lease={settingsTargetLease(() => ({ context, client: { createChannel } }))}
    onClose={vi.fn()} onCreated={vi.fn()} />);
  expect(html).toContain("这台 Mac"); expect(html).toContain("空间连接已变化");
  expect(html).toMatch(/<button(?=[^>]*type="submit")(?=[^>]*disabled)[^>]*>/); expect(createChannel).not.toHaveBeenCalled();
});
