import { describe, expect, it } from "vitest";

import { accountHeadline, type VortoAccountStatus } from "./vortoAccount";

const base: VortoAccountStatus = {
  signedIn: true, email: "me@example.com", deviceName: "我的 Mac", offline: false,
  manageUrl: "https://account.example.com/account", note: "",
};

describe("accountHeadline", () => {
  it("shows who is signed in, and keeps them signed in while offline", () => {
    expect(accountHeadline(base, false)).toBe("me@example.com");
    expect(accountHeadline({ ...base, offline: true }, false)).toContain("暂时连不上");
  });
  it("explains why the user was signed out", () => {
    expect(accountHeadline({ ...base, signedIn: false, email: "", note: "登录已失效" }, false)).toBe("登录已失效");
    expect(accountHeadline({ ...base, signedIn: false, email: "" }, false)).toBe("未登录");
  });
  it("tells the user to finish in the browser while waiting", () => {
    expect(accountHeadline(null, true)).toContain("浏览器");
    expect(accountHeadline(null, false)).toContain("读取");
  });
});
