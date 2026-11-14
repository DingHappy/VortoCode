import { expect, it } from "vitest";
import { desktopContext, retryAssistant } from "./desktopContext";
import type { DesktopProjectProfile } from "../types";
import { AssistantRefreshQueue } from "./assistantRefresh";

const host = (id: string): DesktopProjectProfile => ({ id, kind: "remote", name: "助理", repoRoot: "@assistant", baseUrl: "https://assistant.example", lastOpenedAt: 0 });
const input = { registrationId: "a", assistantId: "a", scope: "general" as const, repoRoot: "", sid: "chat", baseUrl: host("a").baseUrl,
  runtimeId: "", connection: "connected" as const, profiles: [host("a"), host("b")], localResidentId: null };
const source = { registrationId: "a", baseUrl: input.baseUrl, sid: "chat", generation: 1, epoch: 1 };

it("separates same-name same-address registrations, sessions and reconnect reads", () => {
  const a = desktopContext({ ...input, source });
  const b = desktopContext({ ...input, registrationId: "b", assistantId: "b", source: { ...source, registrationId: "b" } });
  expect(a.space?.id).not.toBe(b.space?.id);
  expect(a.draftKey).not.toBe(b.draftKey);
  const reconnected = desktopContext({ ...input, source: { ...source, generation: 2 } });
  expect(a.readKey).not.toBe(reconnected.readKey);
  expect(a.draftKey).toBe(reconnected.draftKey);
  expect(a.draftKey).not.toBe(desktopContext({ ...input, baseUrl: "https://replacement.example", source: null }).draftKey);
  expect(a.draftKey).toBe(desktopContext({ ...input, profiles: [{ ...host("a"), name: "新名字" }], source }).draftKey);
  expect(a.draftKey).not.toBe(desktopContext({ ...input, sid: "other", source: { ...source, sid: "other" } }).draftKey);
});

it("never presents a stale client as the selected space or invents project ownership", () => {
  expect(desktopContext({ ...input, registrationId: "b", assistantId: "b", source }).ready).toBe(false);
  expect(desktopContext({ ...input, sid: "other", source }).ready).toBe(false);
  const project = desktopContext({ ...input, assistantId: null, scope: "project", repoRoot: "/project", source });
  expect(project.space).toBeNull();
  expect(project.location).toContain("项目");
  expect(project.location).toContain("归属待核对");
  expect(project.projectRoot).toBe("/project");
  const local = desktopContext({ ...input, registrationId: null, assistantId: null, baseUrl: "http://127.0.0.1:8080", source: null });
  const moved = desktopContext({ ...input, registrationId: null, assistantId: null, baseUrl: "http://127.0.0.1:9090", source: null });
  expect(local.draftKey).toBe(moved.draftKey);
  expect(local.readKey).not.toBe(moved.readKey);
});

it("rejects queued reads from the first A after A→B→A even when the address and session match", async () => {
  const queue = new AssistantRefreshQueue();
  let finish!: () => void;
  const delayed = new Promise<void>(resolve => { finish = resolve; });
  const oldA = desktopContext({ ...input, source });
  let current = oldA;
  const published: string[] = [];
  const reads = queue.request(source, oldA.readKey, undefined, () => current.readKey === oldA.readKey, async () => {
    await delayed;
    if (current.readKey === oldA.readKey) published.push("stale A");
  });
  const queued = queue.request(source, oldA.readKey, "task", () => current.readKey === oldA.readKey, async () => { published.push("stale task"); });
  current = desktopContext({ ...input, registrationId: "b", assistantId: "b", source: { ...source, registrationId: "b", generation: 2 } });
  const newSource = { ...source, generation: 3 };
  current = desktopContext({ ...input, source: newSource });
  await queue.request(newSource, current.readKey, undefined, () => true, async () => { published.push("current A"); });
  finish(); await Promise.all([reads, queued]);
  expect(published).toEqual(["current A"]);
});

it("retries the addressed assistant after a drop rather than silently selecting the old default", () => {
  expect(retryAssistant(input.profiles, "b")?.id).toBe("b");
  expect(retryAssistant(input.profiles, "removed")).toBeUndefined();
  expect(retryAssistant(input.profiles, null)?.id).toBe("a");
});
