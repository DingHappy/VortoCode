import { describe, expect, it } from "vitest";

import {
  accountHeadline, creditsLow, keySourceLabel, MEMBER_KEY_SOURCE, membershipLine, sidebarIdentity, type VortoAccountStatus,
  type VortoMembership,
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
    creditsAvailable: 6900, creditsPerPeriod: 6900, keyEnabled: true, inUse: false,
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

describe("sidebarIdentity", () => {
  const signedIn: VortoAccountStatus = {
    signedIn: true, email: "me@example.com", deviceName: "Mac", offline: false, manageUrl: "", note: "",
  };
  const member: VortoMembership = {
    available: true, member: true, planName: "中度", expiresAt: 0, creditsAvailable: 3000, creditsPerPeriod: 6900, keyEnabled: true, inUse: true,
  };
  it("shows the signed-in person and their membership", () => {
    expect(sidebarIdentity(signedIn, member)).toEqual({
      initial: "M", name: "me@example.com", detail: "中度会员", title: "已登录 me@example.com · 中度会员",
    });
    expect(sidebarIdentity(signedIn, { ...member, member: false }).detail).toBe("");
    expect(sidebarIdentity({ ...signedIn, offline: true }, null).detail).toBe("离线");
  });
  it("invites signing in, and stays neutral while loading", () => {
    expect(sidebarIdentity({ ...signedIn, signedIn: false, email: "" }, null).name).toBe("登录");
    expect(sidebarIdentity(null, null)).toMatchObject({ initial: "", name: "VortoCode 账号" });
  });
});

describe("creditsLow", () => {
  const member: VortoMembership = {
    available: true, member: true, planName: "中度", expiresAt: new Date(2026, 10, 18, 12).getTime() / 1000,
    creditsAvailable: 600, creditsPerPeriod: 6900, keyEnabled: true, inUse: true,
  };
  it("warns under a tenth of the period, in the card and the sidebar", () => {
    expect(creditsLow(member)).toBe(true);
    expect(membershipLine(member)).toContain("不足一成");
    const signedIn: VortoAccountStatus = { signedIn: true, email: "me@example.com", deviceName: "", offline: false, manageUrl: "", note: "" };
    expect(sidebarIdentity(signedIn, member).detail).toBe("中度会员 · 额度不足");
  });
  it("stays quiet with enough credit, an unknown total, or no membership", () => {
    expect(creditsLow({ ...member, creditsAvailable: 700 })).toBe(false);
    expect(creditsLow({ ...member, creditsPerPeriod: 0 })).toBe(false);
    expect(creditsLow({ ...member, member: false })).toBe(false);
    expect(creditsLow(null)).toBe(false);
  });
});
