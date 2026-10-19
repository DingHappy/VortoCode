import { expect, it } from "vitest";

import { relayMode, settingsBlockVisible } from "./SettingsModal";

it("only shows the relay's account and usage blocks to people actually using that relay", () => {
  expect(relayMode(null)).toBe(false);
  expect(relayMode({ provider: "custom" })).toBe(false);
  expect(relayMode({ provider: "local" })).toBe(false);
  expect(relayMode({ provider: "vortocode" })).toBe(true);
  expect(relayMode({ provider: "custom", account: { username: "me" } })).toBe(true);
});

it("shows the legacy relay login only to people who used it, not to members", () => {
  expect(settingsBlockVisible("relayLogin", { provider: "vortocode" })).toBe(false);
  expect(settingsBlockVisible("relayLogin", { provider: "vortocode", account: { keySource: "token_plan" } })).toBe(true);
  expect(settingsBlockVisible("relayLogin", { provider: "vortocode", account: { keySource: "vortocode_member" } })).toBe(false);
  expect(settingsBlockVisible("usage", { provider: "vortocode", account: { keySource: "vortocode_member" } })).toBe(true);
  expect(settingsBlockVisible("usage", { provider: "custom" })).toBe(false);
});
