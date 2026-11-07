import { expect, it } from "vitest";
import { appShortcut } from "./appShortcuts";

const key = { key: "k", metaKey: true, ctrlKey: false, altKey: false, shiftKey: false,
  defaultPrevented: false, isComposing: false, keyCode: 0 };

it("maps the advertised search and settings shortcuts with either command modifier", () => {
  for (const modifiers of [{ metaKey: true, ctrlKey: false }, { metaKey: false, ctrlKey: true }]) {
    expect(appShortcut({ ...key, ...modifiers }, false)).toBe("search");
    expect(appShortcut({ ...key, ...modifiers, key: "K" }, false)).toBe("search");
    expect(appShortcut({ ...key, ...modifiers, key: "," }, false)).toBe("settings");
  }
});

it("leaves events already handled by an editor alone", () => {
  for (const value of ["k", ","]) expect(appShortcut({ ...key, key: value, defaultPrevented: true }, false)).toBeNull();
});

it("does not navigate behind an open dialog even when its editor did not handle the key", () => {
  for (const value of ["k", ","]) expect(appShortcut({ ...key, key: value }, true)).toBeNull();
});

it("leaves composition and additional modifiers to their current controls", () => {
  for (const value of ["k", ","]) {
    for (const guard of [{ isComposing: true }, { keyCode: 229 }, { altKey: true }, { shiftKey: true }]) {
      expect(appShortcut({ ...key, key: value, ...guard }, false)).toBeNull();
    }
  }
});

it("ignores ordinary typing and other command keys", () => {
  expect(appShortcut({ ...key, metaKey: false }, false)).toBeNull();
  for (const value of ["Enter", "Escape", "Tab", "a"]) expect(appShortcut({ ...key, key: value }, false)).toBeNull();
});
