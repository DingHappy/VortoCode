import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { PersonalSpaceDialog, PersonalSpacePicker } from "./PersonalSpacePicker";
import { desktopContext } from "../lib/desktopContext";
import type { DesktopProjectProfile } from "../types";

const profiles: DesktopProjectProfile[] = ["a", "b", "pending"].map(id => ({ id, name: "同名助手", kind: "remote",
  repoRoot: "@assistant", baseUrl: "https://name:secret@assistant.example", lastOpenedAt: 0, assistantPending: id === "pending" }));
const context = desktopContext({ registrationId: "b", assistantId: "b", scope: "general", repoRoot: "", sid: "s",
  baseUrl: profiles[0].baseUrl, profiles, runtimeId: "", connection: "connected", localResidentId: null,
  source: { registrationId: "a", baseUrl: profiles[0].baseUrl, sid: "s", generation: 1, epoch: 1 } });
const props = { context, profiles, localResidentId: null, switching: false, onSelect: async () => {}, onManage: () => {} };

it("shows the addressed target and truthfully labels a client that is not ready", () => {
  const html = renderToStaticMarkup(<PersonalSpacePicker {...props} connection="connected" />);
  expect(html).toContain("同名助手"); expect(html).toContain("正在核对目标");
  expect(html).toContain('aria-haspopup="dialog"'); expect(html).toContain('aria-expanded="false"');
  expect(html).not.toContain("secret");
});

it("distinguishes same-name spaces by registration and safe origin, excludes pending and preserves focus actions", () => {
  const html = renderToStaticMarkup(<PersonalSpaceDialog {...props} onClose={() => {}} />);
  expect(html).toContain("连接登记：a"); expect(html).toContain("连接登记：b");
  expect(html).toContain("https://assistant.example");
  expect(html).not.toContain("secret"); expect(html).not.toContain("连接登记：pending");
  expect(html.match(/aria-current="true"/g)).toHaveLength(1);
  expect(html).toContain('aria-label="关闭空间选择"');
  expect(html).toContain("管理运行位置");
});
