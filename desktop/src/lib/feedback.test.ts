import { describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

import { feedbackMailto } from "./feedback";

describe("feedbackMailto", () => {
  it("writes to the contact address with version and system prefilled", () => {
    const url = feedbackMailto({ version: "0.2.2", buildId: "b-1", builtAt: "", sourceFingerprint: "" }, "Mac OS X 15");
    expect(url.startsWith("mailto:service@vortotech.com?")).toBe(true);
    const params = new URLSearchParams(url.split("?")[1]);
    expect(params.get("subject")).toBe("VortoOne 0.2.2 问题反馈");
    expect(params.get("body")).toContain("版本：VortoOne 0.2.2（b-1）");
    expect(params.get("body")).toContain("系统：Mac OS X 15");
  });
});

it("sends in-app feedback through the native layer with the three fields", async () => {
  const { sendFeedback } = await import("./feedback");
  const core = await import("@tauri-apps/api/core");
  const invoke = vi.mocked(core.invoke);
  invoke.mockResolvedValueOnce(7 as never);
  await expect(sendFeedback({ text: "更新卡住", contact: "me@x", attachLog: true })).resolves.toBe(7);
  expect(invoke).toHaveBeenLastCalledWith("send_feedback", { text: "更新卡住", contact: "me@x", attachLog: true });
});
