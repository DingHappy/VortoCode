import { expect, it, vi } from "vitest";
import { connectResidentSelection } from "./residentConnection";
import type { DesktopProjectProfile } from "../types";

const profile: DesktopProjectProfile = { id: "fixture-host", name: "测试常驻助手", kind: "remote",
  repoRoot: "@assistant", baseUrl: "http://127.0.0.1:18765", lastOpenedAt: 0 };

it("does not activate a host or open its chat when the connection returns an error string", async () => {
  const activate = vi.fn();
  await expect(connectResidentSelection(profile, async () => "无法连接测试服务", activate)).rejects.toThrow("无法连接测试服务");
  expect(activate).not.toHaveBeenCalled();
});

it("activates exactly the explicitly selected host after confirmed connection", async () => {
  const activate = vi.fn();
  const connect = vi.fn(async () => true as const);
  await connectResidentSelection(profile, connect, activate);
  expect(connect).toHaveBeenCalledExactlyOnceWith(profile);
  expect(activate).toHaveBeenCalledExactlyOnceWith(profile);
});

it("keeps the original selection when connection throws", async () => {
  const activate = vi.fn();
  await expect(connectResidentSelection(profile, async () => { throw new Error("probe unavailable"); }, activate))
    .rejects.toThrow("probe unavailable");
  expect(activate).not.toHaveBeenCalled();
});
