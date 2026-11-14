import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { PreferenceDetails, PreferenceGroup, PreferenceRow } from "./Preferences";

it("keeps optional help collapsed and supplies a keyboard-operable summary", () => {
  const markup = renderToStaticMarkup(<PreferenceDetails label="了解模型配置"><p>常驻助理独立管理模型。</p></PreferenceDetails>);
  expect(markup).toMatch(/<details class="vc-preference-details /);
  expect(markup).toMatch(/<summary>[\s\S]*了解模型配置[\s\S]*<\/summary>/);
  expect(markup.split(">")[0]).not.toContain("open");
});

it("labels a real setting control and exposes current status separately from help", () => {
  const markup = renderToStaticMarkup(<PreferenceGroup title="通知"><PreferenceRow title="桌面通知" htmlFor="notices" status="等待系统授权">
    <input id="notices" type="checkbox" />
  </PreferenceRow></PreferenceGroup>);
  expect(markup).toContain('<label for="notices">桌面通知</label>');
  expect(markup).toContain('id="notices"');
  expect(markup).toMatch(/<span id="([^"]+)" class="vc-preference-status" role="status">等待系统授权<\/span>/);
});
