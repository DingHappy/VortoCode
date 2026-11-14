import { describe, expect, it, vi } from "vitest";
import type { DesktopProjectProfile } from "../types";
import { completeRuntimeSetup, readRuntimeSetup, startupRuntimeRoute, writeRuntimeSetup } from "./runtimeOnboarding";
import { STORAGE_KEYS } from "./storage";

const host: DesktopProjectProfile = { id: "fixture-host", name: "My assistant", kind: "remote", repoRoot: "@assistant", baseUrl: "https://runtime.example", lastOpenedAt: 0 };
const store = () => { const values = new Map<string, string>(); return { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } }; };
const none = { record: null, invalid: false };

describe("runtime onboarding", () => {
  it("shows a new installation a location choice without selecting a runtime", () => {
    expect(startupRuntimeRoute(none, false, [], false, false)).toEqual({ kind: "setup", record: { version: 1, status: "pending", choice: null }, error: "" });
  });
  it("leaves existing users on the original startup path", () => {
    for (const flags of [[true, false, false], [false, true, false], [false, false, true]]) {
      expect(startupRuntimeRoute(none, flags[0], [], flags[1], flags[2]).kind).toBe("existing");
    }
    expect(startupRuntimeRoute(none, false, [host], false, false).kind).toBe("existing");
  });
  it("resumes an unfinished choice even after model configuration was saved", () => {
    const storage = store();
    writeRuntimeSetup(storage, { version: 1, status: "pending", choice: "local" });
    const read = readRuntimeSetup(storage);
    expect(startupRuntimeRoute(read, true, [host], true, false)).toMatchObject({ kind: "setup", record: { choice: "local" } });
    expect(storage.getItem(STORAGE_KEYS.runtimeSetup)).not.toMatch(/apiKey|token|baseUrl/);
  });
  it("restores the exact completed target and never substitutes another host", () => {
    const record = { version: 1, status: "complete", targetId: host.id } as const;
    expect(startupRuntimeRoute({ record, invalid: false }, true, [host], true, true)).toMatchObject({ kind: "restore", target: host });
    for (const profiles of [[], [{ ...host, id: "other" }], [{ ...host, assistantPending: true }]]) {
      expect(startupRuntimeRoute({ record, invalid: false }, true, profiles, true, true)).toMatchObject({ kind: "setup", error: expect.any(String) });
    }
  });
  it("keeps corrupt or unsupported records explicit", () => {
    const storage = store();
    storage.setItem(STORAGE_KEYS.runtimeSetup, '{"version":99,"status":"complete"}');
    const read = readRuntimeSetup(storage);
    expect(read).toEqual({ record: null, invalid: true });
    expect(startupRuntimeRoute(read, true, [host], true, true)).toMatchObject({ kind: "setup", error: expect.stringContaining("记录") });
    expect(storage.getItem(STORAGE_KEYS.runtimeSetup)).toContain('99');
  });
  it("local model read failure does not block a known remote target but cannot prove a new installation", () => {
    expect(startupRuntimeRoute({ record: { version: 1, status: "complete", targetId: host.id }, invalid: false }, false, [host], null, false)).toMatchObject({ kind: "restore", target: host });
    expect(startupRuntimeRoute(none, true, [], null, false)).toEqual({ kind: "existing" });
    expect(startupRuntimeRoute(none, false, [], null, false)).toMatchObject({ kind: "setup", inspectionFailed: true });
  });
  it("marks completion only after successful connection", async () => {
    const storage = store(), connect = vi.fn(async () => { expect(readRuntimeSetup(storage).record).toBeNull(); });
    const done = await completeRuntimeSetup(host, connect, storage, () => true);
    expect(done).toEqual({ version: 1, status: "complete", targetId: host.id });
    expect(readRuntimeSetup(storage).record).toEqual(done);
    expect(connect).toHaveBeenCalledWith(host);
  });
  it("does not complete a failed or superseded connection", async () => {
    const storage = store();
    writeRuntimeSetup(storage, { version: 1, status: "pending", choice: "remote" });
    await expect(completeRuntimeSetup(host, async () => { throw new Error("offline"); }, storage, () => true)).rejects.toThrow("offline");
    let active = true;
    await expect(completeRuntimeSetup(host, async () => { active = false; }, storage, () => active)).rejects.toThrow();
    expect(readRuntimeSetup(storage).record).toMatchObject({ status: "pending", choice: "remote" });
  });
  it("reports connected separately from a failed progress save", async () => {
    const connect = vi.fn(async () => {});
    await expect(completeRuntimeSetup(host, connect, { setItem: () => { throw new Error("full"); } }, () => true)).rejects.toThrow("助手已连接，但引导进度未能保存");
    expect(connect).toHaveBeenCalledOnce();
  });
  it("projects records without persisting extra caller fields", () => {
    const storage = store();
    writeRuntimeSetup(storage, { version: 1, status: "pending", choice: "local", apiKey: "synthetic-extra" } as never);
    expect(JSON.parse(storage.getItem(STORAGE_KEYS.runtimeSetup)!)).toEqual({ version: 1, status: "pending", choice: "local" });
  });
});
