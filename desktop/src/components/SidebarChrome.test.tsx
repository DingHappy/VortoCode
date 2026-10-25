import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import { SidebarBrand, SidebarSectionTitle, WorkNav } from "./SidebarChrome";

const noop = () => {};

it("the search box starts closed", () => {
  const html = renderToStaticMarkup(<SidebarBrand query="" onQuery={noop} />);
  expect(html).toContain('aria-label="搜索会话和项目"');
  expect(html).not.toContain("sidebar-search");
});

it("work nav marks the open page and needs a connection for a new chat", () => {
  const html = renderToStaticMarkup(<WorkNav view="artifacts" connected={false} onNewChat={noop} onWork={noop} onArtifacts={noop} />);
  expect(html).toContain('<button disabled=""><svg');
  expect(html).toMatch(/class="active" title="Agent 做出来的网页和文档"/);
});

it("a folded section says so and keeps its actions", () => {
  const html = renderToStaticMarkup(<SidebarSectionTitle label="最近" className="work-items-heading" open={false} onToggle={noop}>
    <button>+</button></SidebarSectionTitle>);
  expect(html).toContain('class="sidebar-section-title work-items-heading"');
  expect(html).toContain('aria-expanded="false"');
  expect(html).toContain("folded");
  expect(html).toContain("<button>+</button>");
});
