import { expect, it } from "vitest";

import { relayMode } from "./SettingsModal";

it("only shows the relay's account and usage pages to people actually using that relay", () => {
  expect(relayMode(null)).toBe(false);
  expect(relayMode({ provider: "custom" })).toBe(false);
  expect(relayMode({ provider: "local" })).toBe(false);
  expect(relayMode({ provider: "vortocode" })).toBe(true);
  expect(relayMode({ provider: "custom", account: { username: "me" } })).toBe(true);
});
