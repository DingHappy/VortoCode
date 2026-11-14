import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { SettingsTargetSection } from "./SettingsTargetSection";
import { settingsTargetLease } from "../../lib/settingsTarget";
import { desktopContext } from "../../lib/desktopContext";
const context = desktopContext({ registrationId: null, assistantId: null, scope: "project", repoRoot: "/project",
  sid: "s", baseUrl: "http://127.0.0.1:8080", runtimeId: "r", connection: "connected", profiles: [], localResidentId: null,
  source: { registrationId: null, baseUrl: "http://127.0.0.1:8080", sid: "s", generation: 1, epoch: 1 } });

it("keeps local forms available and labels them without claiming remote synchronization", () => {
  const html = renderToStaticMarkup(<SettingsTargetSection owner="desktop"><button>保存</button></SettingsTargetSection>);
  expect(html).toContain("这台 Mac"); expect(html).toContain("生效范围");
  expect(html).not.toContain("disabled"); expect(html).not.toContain("hidden");
});

it("labels the exact project and masks old controls when the same source reconnects", () => {
  const client = {}; let current = context;
  const lease = settingsTargetLease(() => ({ context: current, client }));
  const render = () => renderToStaticMarkup(<SettingsTargetSection owner="project" lease={lease}><button>信任 Hook</button></SettingsTargetSection>);
  expect(render()).toContain("/project"); expect(render()).not.toContain("disabled");
  current = { ...context, readKey: "new generation" };
  const stale = render();
  expect(stale).toContain("原目标设置已锁定");
  expect(stale).toMatch(/<fieldset[^>]+disabled=""[^>]+hidden=""/);
});

it("does not mount a runtime-only form without its owning space", () => {
  let rendered = false;
  function Probe() { rendered = true; return <button>批准</button>; }
  const html = renderToStaticMarkup(<SettingsTargetSection owner="space" unavailable><Probe /></SettingsTargetSection>);
  expect(rendered).toBe(false); expect(html).toContain("当前目标不提供");
});

it("does not start a runtime form's reads before its source has connected", () => {
  let rendered = false;
  function Probe() { rendered = true; return <button>刷新名录</button>; }
  const client = {};
  const lease = settingsTargetLease(() => ({ context: { ...context, ready: false }, client }));
  renderToStaticMarkup(<SettingsTargetSection owner="workspace" lease={lease}><Probe /></SettingsTargetSection>);
  expect(rendered).toBe(false);
});
