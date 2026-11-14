import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { StaffWorkspace } from "./StaffWorkspace";
import { SettingsTargetSection } from "./settings/SettingsTargetSection";
import { settingsTargetLease } from "../lib/settingsTarget";
import { desktopContext } from "../lib/desktopContext";

it("keeps role art visible but hides and locks registry actions after the selected space changes", async () => {
  const profile = { id: "a", name: "个人助手", kind: "remote" as const, repoRoot: "@assistant", baseUrl: "https://assistant.example", lastOpenedAt: 0 };
  const source = { registrationId: "a", baseUrl: profile.baseUrl, sid: "main", generation: 1, epoch: 1 };
  let context = desktopContext({ registrationId: "a", assistantId: "a", scope: "general", repoRoot: "", sid: "main",
    baseUrl: profile.baseUrl, profiles: [profile], runtimeId: "", connection: "connected", localResidentId: null, source });
  const write = vi.fn(async () => {});
  const client = { setStaffStatus: write, desktopSource: source, connectionEpoch: 1 };
  const lease = settingsTargetLease(() => ({ context, client }));
  const render = () => renderToStaticMarkup(<StaffWorkspace onBack={() => {}} onSettings={() => {}}>
    <SettingsTargetSection owner="space" lease={lease}><button>批准登记</button></SettingsTargetSection>
  </StaffWorkspace>);
  expect(render()).toContain("批准登记");
  context = { ...context, readKey: "another-space" };
  const stale = render();
  expect(stale).toContain("角色家族");
  expect(stale).toContain("disabled");
  expect(stale).toContain('<fieldset class="settings-target-fields" disabled="" hidden="">');
  await expect(lease.client!.setStaffStatus()).rejects.toThrow("设置目标已变化");
  expect(write).not.toHaveBeenCalled();
});
