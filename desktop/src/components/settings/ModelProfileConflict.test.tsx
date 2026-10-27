import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ModelProfileConflict } from "./ModelProfileConflict";

it("preserves drafts and requires inspecting the saved profile before adoption", () => {
  const html = renderToStaticMarkup(<ModelProfileConflict onUseLatest={() => { throw Error("must not auto-adopt"); }} />);
  expect(html).toContain("当前草稿仍保留");
  expect(html).toContain("核对最新配置");
  expect(html).not.toContain("放弃草稿并载入最新配置");
  expect(html).not.toContain("API Key");
});
