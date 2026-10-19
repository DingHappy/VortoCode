import { describe, expect, it } from "vitest";
import { feedbackMailto } from "./feedback";

describe("feedbackMailto", () => {
  it("writes to the contact address with version and system prefilled", () => {
    const url = feedbackMailto({ version: "0.2.2", buildId: "b-1", builtAt: "", sourceFingerprint: "" }, "Mac OS X 15");
    expect(url.startsWith("mailto:service@vortotech.com?")).toBe(true);
    const params = new URLSearchParams(url.split("?")[1]);
    expect(params.get("subject")).toBe("VortoCode 0.2.2 问题反馈");
    expect(params.get("body")).toContain("版本：VortoCode 0.2.2（b-1）");
    expect(params.get("body")).toContain("系统：Mac OS X 15");
  });
});
