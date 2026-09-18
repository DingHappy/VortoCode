import { describe, expect, it } from "vitest";

import { errorText } from "./errorText";

describe("errorText", () => {
  it("keeps the reason a rejected Tauri invoke carries as a bare string", () => {
    // 真机（2026-09-17）：选了非 Git 目录，后端如实回 "请选择 Git 工作区：fatal: ..."，
    // 旧写法 `instanceof Error` 不成立，界面只剩一句"项目目录不可用"。
    const cause = "请选择 Git 工作区：fatal: not a git repository";
    expect(errorText(cause, "项目目录不可用")).toBe(cause);
  });

  it("uses Error.message", () => {
    expect(errorText(new Error("boom"), "fallback")).toBe("boom");
  });

  it("reads message/error off plain objects", () => {
    expect(errorText({ message: "从对象里拿到的原因" }, "fallback")).toBe("从对象里拿到的原因");
    expect(errorText({ error: "另一种形状" }, "fallback")).toBe("另一种形状");
  });

  it("falls back for empty or unusable causes", () => {
    expect(errorText(new Error("   "), "fallback")).toBe("fallback");
    expect(errorText("", "fallback")).toBe("fallback");
    expect(errorText(null, "fallback")).toBe("fallback");
    expect(errorText(undefined, "fallback")).toBe("fallback");
    expect(errorText({ code: 500 }, "fallback")).toBe("fallback");
  });
});
