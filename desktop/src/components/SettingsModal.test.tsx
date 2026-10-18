import { expect, it } from "vitest";

import { relayMode, sectionVisible } from "./SettingsModal";

it("only shows the relay's account and usage pages to people actually using that relay", () => {
  expect(relayMode(null)).toBe(false);
  expect(relayMode({ provider: "custom" })).toBe(false);
  expect(relayMode({ provider: "local" })).toBe(false);
  expect(relayMode({ provider: "vortocode" })).toBe(true);
  expect(relayMode({ provider: "custom", account: { username: "me" } })).toBe(true);
});

it("shows the legacy relay login page only to people who used it, not to members", () => {
  expect(sectionVisible("account", { provider: "vortocode" })).toBe(false);
  expect(sectionVisible("account", { provider: "vortocode", account: { keySource: "token_plan" } })).toBe(true);
  expect(sectionVisible("account", { provider: "vortocode", account: { keySource: "vortocode_member" } })).toBe(false);
  expect(sectionVisible("usage", { provider: "vortocode", account: { keySource: "vortocode_member" } })).toBe(true);
  expect(sectionVisible("usage", { provider: "custom" })).toBe(false);
  expect(sectionVisible("general", null)).toBe(true);
});
