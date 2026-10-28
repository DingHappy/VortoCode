import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import { SidebarBrand, SidebarSearch, SidebarSectionTitle, WorkNav } from "./SidebarChrome";

const noop = () => {};

it("the brand row offers collapsing the sidebar", () => {
  expect(renderToStaticMarkup(<SidebarBrand onCollapse={noop} />)).toContain('aria-label="收起侧栏"');
});

it("search is a nav item with its shortcut until opened", () => {
  const closed = renderToStaticMarkup(<SidebarSearch open={false} query="" placeholder="搜索聊天" onOpenChange={noop} onQuery={noop} />);
  expect(closed).toContain("搜索");
  expect(closed).toContain("⌘K");
  expect(closed).not.toContain("<input");
  const open = renderToStaticMarkup(<SidebarSearch open query="周报" placeholder="搜索聊天" onOpenChange={noop} onQuery={noop} />);
  expect(open).toContain('aria-label="搜索聊天"');
  expect(open).toContain('value="周报"');
});

it("work nav has four pages, marks the open one, and needs a connection for a new chat", () => {
  const html = renderToStaticMarkup(<WorkNav page="library" connected={false} onNewChat={noop} onPage={noop} />);
  expect(html).toMatch(/disabled=""[^>]*class="vc-nav-item"><svg/);
  expect(html).toMatch(/title="Agent 做出来的网页和文档" type="button" aria-current="page"/);
  expect(html.match(/aria-current/g)).toHaveLength(1);
  for (const label of ["任务", "目标", "资料库", "扩展"]) expect(html).toContain(`>${label}</span>`);
  expect(html).not.toContain("vc-count");
  expect(renderToStaticMarkup(<WorkNav page={null} connected onNewChat={noop} onPage={noop} waiting={2} />))
    .toContain('<span class="vc-count" aria-label="2 件等你处理">2</span>');
});

it("a folded section says so and keeps its actions", () => {
  const html = renderToStaticMarkup(<SidebarSectionTitle label="最近" className="work-items-heading" open={false} onToggle={noop}>
    <button>+</button></SidebarSectionTitle>);
  expect(html).toContain('class="sidebar-section-title work-items-heading"');
  expect(html).toContain('aria-expanded="false"');
  expect(html).toContain("folded");
  expect(html).toContain("<button>+</button>");
});
