import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { WorkspaceNavigation } from "./WorkspaceNavigation";
import { AssistantPanel, AssistantSidebar } from "./AssistantMode";

it("opens existing daily views without adding another waiting count", () => {
  const markup = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "main" }} current="main" sideChats={[]} page="tasks" waiting={4}
    onSelect={() => {}} onPage={() => {}} onNewSideChat={() => {}} />);
  expect(markup).toContain("工作动态");
  expect(markup).toContain("定时工作");
  expect(markup.match(/4 件等你处理/g)).toHaveLength(1);
  expect(markup.match(/aria-current="page"/g)).toHaveLength(1);
});

it("makes a directly opened secondary view visible in the expanded section", () => {
  const markup = renderToStaticMarkup(<WorkspaceNavigation current="audit" onPage={() => {}} />);
  expect(markup).toContain('aria-expanded="true"');
  expect(markup).toMatch(/aria-current="page"[\s\S]*?审核记录/);
});

it("uses the same panel content as a full page with an explicit route back", () => {
  const markup = renderToStaticMarkup(<AssistantPanel presentation="page" name="演示助手" connected host={{ kind: "local", canAddResident: false }}
    onAddResident={() => {}} onRetryResident={() => {}} tab="schedule" onTab={() => {}} onClose={() => {}}><p>现有排期记录</p></AssistantPanel>);
  expect(markup).toContain("assistant-panel--page");
  expect(markup).toContain("返回对话");
  expect(markup).toContain("现有排期记录");
  expect(markup).toContain('role="region"');
  expect(markup).not.toContain('role="tablist"');
  expect(markup).not.toContain('aria-label="收起面板"');
});
