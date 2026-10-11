import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { BuildVersion } from "./BuildVersion";
const info = { version: "0.1.1", buildId: "20261009T000000000Z-12345678", builtAt: "2026-10-09T00:00:00Z", sourceFingerprint: "12345678".repeat(8) };
it("shows the loaded build in compact and settings forms", () => {
  const compact = renderToStaticMarkup(<BuildVersion info={info} />);
  expect(compact).toContain("v0.1.1"); expect(compact).toContain(info.buildId);
  const detail = renderToStaticMarkup(<BuildVersion info={info} details />);
  expect(detail).toContain("构建时间"); expect(detail).toContain(info.buildId); expect(detail).toContain("退出再打开");
});
