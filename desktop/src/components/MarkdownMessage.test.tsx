import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MarkdownMessage } from "./MarkdownMessage";

const render = (text: string) => renderToStaticMarkup(<MarkdownMessage text={text} />);
describe("daily reply tables", () => {
  it("renders the reported weather reply after a paragraph as a semantic table", () => {
    const html = render("天气如下：\n| 时段 | 天气 | 温度 |\n|---|---|---:|\n|早晨|☀️ **晴**|22°C|\n|中午|多云|25°C|\n\n来源：[页面](https://example.com)");
    expect(html).toContain('<table>'); expect(html).toContain('scope="col"');
    expect(html).toContain('<strong>晴</strong>'); expect(html).toContain('text-align:right');
    expect(html).toContain('href="https://example.com"'); expect(html).not.toContain('|---|');
  });
  it("keeps code fences literal and rejects malformed table delimiters", () => {
    expect(render('```text\n| a | b |\n|---|---|\n```')).not.toContain('<table>');
    expect(render('| a | b |\n|---|not a delimiter|')).not.toContain('<table>');
    expect(render('| a | b |\n|---|')).not.toContain('<table>');
  });
  it("handles optional borders, escaped pipes, missing and extra cells", () => {
    const html = render('a | b\n:--- | :---:\nx\\|y | `z`\nshort |\none | two | extra');
    expect(html).toContain('x|y'); expect(html).toContain('<code>z</code>');
    expect(html).toContain('<td style="text-align:center"></td>');
    expect(html).not.toContain('extra');
  });
  it("never interprets table content as HTML or allows unsafe links", () => {
    const html = render('| a | b |\n|---|---|\n|<img src=x onerror=alert(1)>|[bad](javascript:alert)|');
    expect(html).not.toContain('<img'); expect(html).not.toContain('href="javascript:');
    expect(html).toContain('&lt;img');
  });
  it("keeps a following heading and paragraph outside the table", () => {
    const html = render('| a | b |\n|---|---|\n|one|two|\n## 来源\n已核实');
    expect(html).toContain('</table>'); expect(html).toContain('md-h2'); expect(html).toContain('已核实');
  });
  it("keeps malformed fences as text without stalling the reply renderer", () => {
    const html = render('说明\n```python extra-info\n内容\n````\n结束');
    expect(html).toContain('extra-info'); expect(html).toContain('结束');
  });
});
