import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { MemorySnapshotDialog } from "./MemorySnapshotDialog";
import { snapshotFixture } from "../test/memorySnapshotFixture";

it("keeps download disabled while reading or after failure", () => {
  const html = renderToStaticMarkup(<MemorySnapshotDialog snapshot={null} busy error="" onRetry={() => {}} onClose={() => {}} />);
  expect(html).toContain("正在读取记忆");
  expect(html).toContain("disabled=\"\"");
});

it("previews literal markdown with visible scope and incomplete coverage", () => {
  const html = renderToStaticMarkup(<MemorySnapshotDialog busy={false} error="" onRetry={() => {}} onClose={() => {}}
    snapshot={{ ...snapshotFixture(), scope: "实例 A", total: 2,
      coverage_limited: true, markdown: "<script>do_not_run()</script>" }} />);
  expect(html).toContain("已读取 1 / 2 条");
  expect(html).toContain("部分记录未导出");
  expect(html).toContain("&lt;script&gt;");
  expect(html).not.toContain("<script>");
});
