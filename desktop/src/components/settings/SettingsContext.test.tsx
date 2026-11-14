import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { SettingsContext } from "./SettingsContext";
import { desktopContext } from "../../lib/desktopContext";
import { settingsTargetLease } from "../../lib/settingsTarget";

function fixture() {
  const client = {};
  let context = desktopContext({ registrationId: "fixture-registration", assistantId: null, scope: "general", repoRoot: "",
    sid: "fixture-session", baseUrl: "http://127.0.0.1:8080", runtimeId: "general", connection: "connected", profiles: [], localResidentId: null,
    source: { registrationId: "fixture-registration", baseUrl: "http://127.0.0.1:8080", sid: "fixture-session", generation: 1, epoch: 1 } });
  const lease = settingsTargetLease(() => ({ context, client }));
  return { lease, invalidate: () => { context = { ...context, readKey: "reconnected" }; } };
}

it("keeps the registration reference inside closed connection details", () => {
  const { lease } = fixture();
  const markup = renderToStaticMarkup(<SettingsContext lease={lease} scope="保存在这台 Mac" />);
  const details = markup.match(/<details\b[^>]*>[\s\S]*?<\/details>/)?.[0];
  expect(details).toContain("fixture-registration");
  expect(details).toContain("保存在这台 Mac");
  expect(details?.split(">")[0]).not.toContain("open");
  expect(markup.replace(details!, "")).not.toContain("fixture-registration");
});

it("announces an invalid target outside the collapsed details", () => {
  const { lease, invalidate } = fixture(); invalidate();
  const markup = renderToStaticMarkup(<SettingsContext lease={lease} scope="个人空间" />);
  const visible = markup.replace(/<details\b[\s\S]*?<\/details>/, "");
  expect(visible).toContain('role="status"');
  expect(visible).toContain("运行端设置已停用");
});

it("keeps a missing connection explicit rather than naming a different space", () => {
  const markup = renderToStaticMarkup(<SettingsContext scope="仅保存在本机" />);
  expect(markup).toContain("尚未连接");
  expect(markup).toContain("目标尚未核对");
});
