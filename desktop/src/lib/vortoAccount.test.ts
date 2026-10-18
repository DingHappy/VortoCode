import { describe, expect, it } from "vitest";

import {
  accountHeadline, keySourceLabel, MEMBER_KEY_SOURCE, membershipLine, type VortoAccountStatus, type VortoMembership,
} from "./vortoAccount";

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

describe("membershipLine", () => {
  const member: VortoMembership = {
    available: true, member: true, planName: "中度", expiresAt: new Date(2026, 10, 18, 12).getTime() / 1000,
    creditsAvailable: 6900, keyEnabled: true, inUse: false,
  };
  it("summarises plan, expiry and remaining credit", () => {
    expect(membershipLine(member)).toBe("会员「中度」· 到期 2026-11-18 · 本期剩余 6,900 Credit");
  });
  it("distinguishes never-member, expired and a service without membership", () => {
    expect(membershipLine({ ...member, member: false, planName: "" })).toBe("还不是会员");
    expect(membershipLine({ ...member, member: false })).toBe("会员「中度」已到期");
    expect(membershipLine({ ...member, available: false })).toBeNull();
    expect(membershipLine(null)).toBeNull();
  });
});

describe("keySourceLabel", () => {
  it("names where the model key came from", () => {
    expect(keySourceLabel(MEMBER_KEY_SOURCE)).toBe("VortoCode 会员");
    expect(keySourceLabel("token_plan")).toBe("Token Plan");
    expect(keySourceLabel("pay_as_you_go")).toBe("按量 Key");
  });
});
