import { describe, expect, it } from "vitest";
import type { DesktopProjectProfile } from "../types";
import { personalSpaceDirectory } from "./personalSpace";
import { STORAGE_KEYS } from "./storage";

const profile = (id: string, kind: "local" | "remote", repoRoot: string): DesktopProjectProfile => ({
  id, name: "Same name", kind, repoRoot, baseUrl: "https://runtime.example", lastOpenedAt: 0,
});

describe("legacy personal space directory", () => {
  it("keeps the local engine and registered assistants distinct with their original chat keys", () => {
    const first = profile("host-a", "remote", "@assistant");
    const second = profile("host-b", "remote", "@assistant");
    const directory = personalSpaceDirectory([first, second]);
    expect(directory.spaces.map(s => [s.runtime.kind, s.runtime.registrationId, s.mainChatKey])).toEqual([
      ["local-engine", null, STORAGE_KEYS.mainChatSid],
      ["registered-assistant", "host-a", `${STORAGE_KEYS.mainChatSid}:host-a`],
      ["registered-assistant", "host-b", `${STORAGE_KEYS.mainChatSid}:host-b`],
    ]);
    expect(new Set(directory.spaces.map(s => s.id)).size).toBe(3);
    expect(directory.spaces[1].profile).toBe(first);
  });

  it("never infers a project's parent space from matching names, paths or server addresses", () => {
    const host = profile("host", "remote", "@assistant");
    const local = profile("local", "local", "/work/shared");
    const remote = profile("remote", "remote", "/work/shared");
    const directory = personalSpaceDirectory([host, local, remote]);
    expect(directory.projects).toEqual([
      { registrationId: "local", personalSpaceId: null, profile: local },
      { registrationId: "remote", personalSpaceId: null, profile: remote },
    ]);
    expect(directory.spaces).toHaveLength(2);
  });

  it("excludes pending assistants without treating them as projects and does not mutate old records", () => {
    const pending = Object.freeze({ ...profile("pending", "remote", "@assistant"), assistantPending: true });
    const namedDirectory = Object.freeze(profile("directory", "local", "@assistant"));
    const records = Object.freeze([pending, namedDirectory]);
    const directory = personalSpaceDirectory(records);
    expect(directory.spaces.map(s => s.runtime.registrationId)).toEqual([null]);
    expect(directory.projects.map(p => p.registrationId)).toEqual(["directory"]);
    expect(records).toEqual([pending, namedDirectory]);
    const active = personalSpaceDirectory([{ ...pending, assistantPending: false }]);
    expect(active.spaces[1].runtime.registrationId).toBe("pending");
  });
});
