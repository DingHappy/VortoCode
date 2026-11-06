import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { ResultDocument } from "./ResultDocument";

it("renders readable structure while escaping external HTML and blocking executable links", () => {
  const html = renderToStaticMarkup(<ResultDocument title="交付原文" text={'## 报告\n\n<b>外部标签</b>\n\n[不要执行](javascript:alert(1))\n\n|项目|结果|\n|---|---|\n|示例|待核对|'} tainted open />);
  expect(html).toContain("<table>"); expect(html).toContain("&lt;b&gt;外部标签&lt;/b&gt;");
  expect(html).not.toContain("<b>外部标签</b>"); expect(html).not.toContain('href="javascript:');
  expect(html).toContain("阅读不会批准"); expect(html).toContain("查看原文");
});
