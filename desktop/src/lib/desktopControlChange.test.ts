import { expect, it } from "vitest";
import { desktopControlChange } from "./desktopControlChange";
const saved = { enabled: true, target: "ssh:saved-host", apps: "firefox" };
const draft = { target: "ssh:unsaved-host", apps: "invalid; app" };
it("turns off with saved values so an invalid draft cannot block disabling", () => {
  expect(desktopControlChange(saved, draft, "toggle")).toEqual({ args: { ...saved, enabled: false }, keepDraft: true, restart: true });
});
it("saves connection metadata while disabled without enabling desktop operations", () => {
  expect(desktopControlChange({ ...saved, enabled: false }, draft, "save")).toEqual({ args: { ...draft, enabled: false }, keepDraft: false, restart: false });
});
it("an explicit enable uses the current draft for native validation and consent", () => {
  expect(desktopControlChange({ ...saved, enabled: false }, draft, "toggle")).toEqual({ args: { ...draft, enabled: true }, keepDraft: false, restart: true });
});
it("applying a draft while enabled keeps the existing permission enabled and requests restart", () => {
  expect(desktopControlChange(saved, draft, "save")).toEqual({ args: { ...draft, enabled: true }, keepDraft: false, restart: true });
});
