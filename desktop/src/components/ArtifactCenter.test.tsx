import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import type { ArtifactMeta } from "../types";
import { ArtifactCenter } from "./ArtifactCenter";

const today = new Date();
const stamp = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}T09:00:00`;
const artifact = (id: string, kind = "html"): ArtifactMeta =>
  ({ id, title: `标题 ${id}`, kind, version: 2, created_at: stamp(today), updated_at: stamp(today), bytes: 2048, url: "" });

const page = (patch: Partial<Parameters<typeof ArtifactCenter>[0]> = {}) => renderToStaticMarkup(
  <ArtifactCenter artifacts={[]} loading={false} error="" selectedId="" version={null} versions={null}
    previewLoading={false} previewHtml="" onRefresh={() => {}} onOpen={() => {}} onVersion={() => {}} onOpenExternal={() => {}} {...patch} />);

it("empty library says what will appear and how", () => {
  expect(page()).toContain("还没有资料");
  expect(page({ loading: true })).toContain("正在读取");
});

it("lists items grouped by day as real buttons, with kind, version and size", () => {
  const html = page({ artifacts: [artifact("a"), artifact("b", "markdown")] });
  expect(html).toContain("<h1>资料库</h1>");
  expect(html).toContain("今天");
  expect(html).toContain("网页 · v2 · 2.0 KiB");
  expect(html).toContain("文档 · v2");
  expect(html.match(/<button type="button" class="vc-library-row"/g)).toHaveLength(2);
});

it("a remembered selection alone does not open the preview: only a click in this page does", () => {
  const html = page({ artifacts: [artifact("a")], selectedId: "a", previewHtml: "<p>x</p>" });
  expect(html).not.toContain("vc-library-detail");
  expect(html).not.toContain("has-detail");
});
