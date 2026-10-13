import { describe, expect, it, vi } from "vitest";
import { resolveMainChat, sourceLabel } from "./mainChat";

describe("resolveMainChat", () => {
  it("服务器登记过就用服务器那段，不再登记本机的", async () => {
    const claim = vi.fn();
    expect(await resolveMainChat({ getMainChat: async () => "server-sid", claimMainChat: claim }, "local-sid")).toBe("server-sid");
    expect(claim).not.toHaveBeenCalled();
  });

  it("还没登记：把本机这段报上去（并发时以服务器回的为准）", async () => {
    const claim = vi.fn(async () => "first-wins");
    expect(await resolveMainChat({ getMainChat: async () => null, claimMainChat: claim }, "local-sid")).toBe("first-wins");
    expect(claim).toHaveBeenCalledWith("local-sid");
  });

  it("旧版常驻电脑 / 连不上：照旧用本机那段", async () => {
    const broken = { getMainChat: async () => { throw new Error("404"); }, claimMainChat: async () => "x" };
    expect(await resolveMainChat(broken, "local-sid")).toBe("local-sid");
  });
});

describe("sourceLabel", () => {
  it("手机上来的有标记，电脑上的没有", () => {
    expect(sourceLabel("telegram")).toBe("📱 Telegram");
    expect(sourceLabel(undefined)).toBeNull();
    expect(sourceLabel("other-im")).toBe("📱 手机");
  });
});
