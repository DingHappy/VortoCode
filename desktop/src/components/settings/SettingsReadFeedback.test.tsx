import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { SettingsReadFeedback } from "./SettingsReadFeedback";
import { DesktopControlPane } from "./DesktopControlPane";
it("offers retry after read failure rather than showing endless loading", () => {
  const markup = renderToStaticMarkup(<SettingsReadFeedback label="桌面设置" loading={false} error="无法读取" onRetry={() => {}}/>);
  expect(markup).toContain('role="alert"'); expect(markup).toContain("重试读取桌面设置"); expect(markup).not.toContain("正在读取");
});
it("does not render feedback after successful read", () => {
  expect(renderToStaticMarkup(<SettingsReadFeedback label="设置" loading={false} error="" onRetry={() => {}}/>)).toBe("");
});
it("does not allow editing or imply a disabled permission before the first read completes", () => {
  const markup = renderToStaticMarkup(<DesktopControlPane onRestartRuntime={async () => false}/>);
  expect(markup).toContain("状态待读取"); expect(markup).not.toContain("配置已关闭");
  expect(markup.match(/<input[^>]*aria-label="目标桌面"[^>]*>/)?.[0]).toContain("disabled");
});
