import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { RuntimeOnboarding, type RuntimeOnboardingProps } from "./RuntimeOnboarding";
import { OnboardingModel } from "./OnboardingModel";
const noop = () => {};
const props: RuntimeOnboardingProps = { initial: { version: 1, status: "pending", choice: null }, profiles: [], modelReady: false, modelSetup: null,
  onStatus: noop, onInspectAgain: noop, onConnect: async () => {}, onComplete: noop };
it("offers two explicit locations and explains their lifecycles", () => {
  const html = renderToStaticMarkup(<RuntimeOnboarding {...props} />);
  expect(html).toContain('在这台 Mac 运行');
  expect(html).toContain('连接已有助手');
  expect(html).toContain('睡眠时暂停');
  expect(html).toContain('aria-labelledby="runtime-onboarding-title"');
  expect(html).not.toContain('checked');
});
it("keeps an unknown inspection explicit and prevents choosing before retry", () => {
  const html = renderToStaticMarkup(<RuntimeOnboarding {...props} inspectionFailed initialError="读取失败" />);
  expect(html).toContain('role="alert"');
  expect(html).toContain('重新核对已有配置');
  expect(html.match(/<button\b(?=[^>]*\bdisabled="")[^>]*>/g)).toHaveLength(2);
});
it("remote setup omits project paths and does not claim to copy local model configuration", () => {
  const html = renderToStaticMarkup(<RuntimeOnboarding {...props} initial={{ version: 1, status: "pending", choice: "remote" }} />);
  expect(html).toContain('不会复制这台 Mac 的模型配置');
  expect(html).toContain('type="password"');
  expect(html).not.toContain('服务器上的项目目录');
});
it("model setup uses saved-configuration language and blocks saving during failed reads", () => {
  const html = renderToStaticMarkup(<OnboardingModel profile={null} baseUrl="" model="" apiKey="" fastModel="" strongModel="" roleModels={{}}
    loading={false} busy={false} conflict={false} error="读取失败" note="" onBaseUrl={noop} onModel={noop} onKey={noop} onSave={noop} onReload={noop} onAccount={noop} />);
  expect(html).toContain('读取失败');
  expect(html.match(/<button[^>]*>保存模型配置<\/button>/)?.[0]).toContain('disabled');
  expect(html).toContain('放弃草稿并重新载入');
});
