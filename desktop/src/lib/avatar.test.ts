import { describe, expect, it } from "vitest";
import { avatarHue, avatarInitial, avatarSrc } from "./avatar";

describe("avatar", () => {
  it("只接受三种位图，拼成 data URL", () => {
    expect(avatarSrc({ type: "image/png", data: "iVBORw0KGgo=" })).toBe("data:image/png;base64,iVBORw0KGgo=");
    expect(avatarSrc({ type: "image/svg+xml", data: "PHN2Zz4=" })).toBeNull();
    expect(avatarSrc({ type: "image/png", data: "x\" onerror=\"alert(1)" })).toBeNull();
    expect(avatarSrc({ type: "image/png", data: "" })).toBeNull();
    expect(avatarSrc(null)).toBeNull();
  });

  it("首字按字符切：中文、emoji 不切坏，空名字有兜底", () => {
    expect(avatarInitial("小V")).toBe("小");
    expect(avatarInitial("vorto")).toBe("V");
    expect(avatarInitial("😀 bot")).toBe("😀");
    expect(avatarInitial("  ")).toBe("V");
  });

  it("颜色由名字决定：同名同色", () => {
    expect(avatarHue("小V")).toBe(avatarHue("小V"));
    expect(avatarHue("小V")).toBeGreaterThanOrEqual(0);
    expect(avatarHue("小V")).toBeLessThan(360);
  });
});
