import { describe, expect, it } from "vitest";

import { applyThemePreference, readThemePreference, saveThemePreference } from "./theme";

function memoryStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => { data.set(key, value); },
    data,
  };
}

function fakeRoot() {
  const attrs = new Map<string, string>();
  return {
    setAttribute: (name: string, value: string) => { attrs.set(name, value); },
    removeAttribute: (name: string) => { attrs.delete(name); },
    attrs,
  };
}

describe("theme preference", () => {
  it("defaults to following the system and round-trips explicit choices", () => {
    const storage = memoryStorage();
    expect(readThemePreference(storage)).toBe("system");
    saveThemePreference("dark", storage);
    expect(readThemePreference(storage)).toBe("dark");
  });

  it("treats unknown or unreadable values as system", () => {
    expect(readThemePreference(memoryStorage({ "vortocode.desktop.theme": "neon" }))).toBe("system");
    const broken = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("denied"); } };
    expect(readThemePreference(broken)).toBe("system");
    expect(() => saveThemePreference("light", broken)).not.toThrow();
  });

  it("writes data-theme only for explicit light or dark", () => {
    const root = fakeRoot();
    applyThemePreference("dark", root);
    expect(root.attrs.get("data-theme")).toBe("dark");
    applyThemePreference("system", root);
    expect(root.attrs.has("data-theme")).toBe(false);
  });
});
