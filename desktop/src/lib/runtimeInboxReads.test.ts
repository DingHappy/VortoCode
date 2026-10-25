import { expect, it, vi } from "vitest";
import type { DesktopProjectProfile, DesktopRuntimeInbox, GatewayProcessStatus, RuntimeInboxSnapshot } from "../types";
import {
  mergeRuntimeInboxes, observeRuntimeInboxes, runtimeInboxSourceKey, runtimeInboxSources, type RuntimeInboxSource,
} from "./runtimeInboxReads";

const a: RuntimeInboxSource = { runtimeId: "a", scope: "project", repoRoot: "/a", label: "A", baseUrl: "http://127.0.0.1:8080" };
const b: RuntimeInboxSource = { ...a, runtimeId: "b", repoRoot: "/b", label: "B", baseUrl: "http://127.0.0.1:8081" };
const snapshot: RuntimeInboxSnapshot = {
  version: 1, scope: "project", generated_at: "2026-10-23T12:00:00Z",
  sessions: [], decisions: [], goals: [], tasks: [],
  counts: { decisions: 0, hook_issues: 0, goals_blocked: 0, goals_active: 0, tasks_attention: 0,
    tasks_active: 1, sessions_working: 0, sessions_queued: 0, sessions_needing_input: 0 },
};
const observation = (source = a): DesktopRuntimeInbox => ({ ...source, snapshot, error: "", checkedAt: 1 });
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
};

it("publishes healthy runtimes before a slow runtime completes", async () => {
  const slow = deferred<RuntimeInboxSnapshot>(), published: DesktopRuntimeInbox[] = [];
  const ready = deferred<void>();
  const pending = observeRuntimeInboxes([a, b], source => source.runtimeId === "a" ? slow.promise : Promise.resolve(snapshot),
    () => true, item => { published.push(item); ready.resolve(); });
  await ready.promise;
  expect(published.map(item => item.runtimeId)).toEqual(["b"]);
  slow.resolve(snapshot);
  expect(await pending).toBe(true);
  expect(published.map(item => item.runtimeId)).toEqual(["b", "a"]);
});

it("publishes per-runtime failures and backs off only when all sources are unreachable", async () => {
  const publish = vi.fn();
  expect(await observeRuntimeInboxes([a, b], async source => {
    if (source.runtimeId === "a") throw new Error("offline");
    return snapshot;
  }, () => true, publish)).toBe(true);
  expect(publish).toHaveBeenCalledWith(expect.objectContaining({ runtimeId: "a", error: "offline", snapshot: null }));
  expect(await observeRuntimeInboxes([a], async () => { throw new Error("offline"); }, () => true, publish)).toBe(false);
});

it("does not publish late results or failures from an obsolete source generation", async () => {
  const slow = deferred<RuntimeInboxSnapshot>(), publish = vi.fn();
  let current = true;
  const pending = observeRuntimeInboxes([a], () => slow.promise, () => current, publish);
  current = false;
  slow.resolve(snapshot);
  await pending;
  expect(publish).not.toHaveBeenCalled();
  await observeRuntimeInboxes([a], async () => { throw new Error("old error"); }, () => false, publish);
  expect(publish).not.toHaveBeenCalled();
});

it("retains historical snapshots on failure and replaces them after recovery", () => {
  const old = observation();
  const failed = mergeRuntimeInboxes([old], [a], { ...old, snapshot: null, error: "offline", checkedAt: 2 });
  expect(failed[0]).toMatchObject({ snapshot, error: "offline", checkedAt: 2 });
  const recovered = { ...snapshot, generated_at: "2026-10-23T12:01:00Z" };
  expect(mergeRuntimeInboxes(failed, [a], { ...old, snapshot: recovered, checkedAt: 3 })[0])
    .toMatchObject({ snapshot: recovered, error: "", checkedAt: 3 });
});

it("updates labels and preserves source order while removing retired sources", () => {
  const ordered = mergeRuntimeInboxes([observation(a), observation(b)], [{ ...b, label: "renamed" }, a]);
  expect(ordered.map(item => item.runtimeId)).toEqual(["b", "a"]);
  expect(ordered[0].label).toBe("renamed");
  expect(mergeRuntimeInboxes(ordered, [])).toEqual([]);
});

it.each([
  { baseUrl: "http://127.0.0.1:9000" }, { repoRoot: "/different" }, { scope: "general" as const },
  { projectId: "new" }, { workspaceId: "new" },
])("never carries observations over a changed runtime identity: %j", changed => {
  const source = { ...a, ...changed };
  expect(mergeRuntimeInboxes([observation()], [source], observation())).toEqual([]);
  expect(mergeRuntimeInboxes([observation()], [source], { ...source, snapshot: null, error: "offline", checkedAt: 2 })[0].snapshot).toBeNull();
});

it("asks running managed runtimes plus registered remote workspaces and the resident assistant", () => {
  const project = (id: string, name: string, kind: "local" | "remote", repoRoot: string, baseUrl = ""): DesktopProjectProfile =>
    ({ id, name, kind, repoRoot, baseUrl, lastOpenedAt: 0 });
  const projects = [
    project("p1", "VortoCode", "local", "/code/vorto"),
    project("r1", "服务器", "remote", "/srv/app", "https://srv.example"),
    project("h1", "家里", "remote", "@assistant", "https://home.example"),
  ];
  const running = (patch: Partial<GatewayProcessStatus>): GatewayProcessStatus => ({ running: true, message: "", baseUrl: "http://127.0.0.1:9000", ...patch });
  const sources = runtimeInboxSources([
    running({ runtimeId: "general" }),
    running({ runtimeId: "rt-proj", projectId: "p1", repoRoot: "/code/vorto" }),
    running({ runtimeId: "rt-scratch-abcdef", workspaceId: "ws123456789" }),
    running({ runtimeId: "rt-other", repoRoot: "/code/unknown-repo" }),
    { ...running({ runtimeId: "rt-stopped" }), running: false },
    running({ runtimeId: "rt-no-url", baseUrl: undefined }),
  ], projects);
  expect(sources.map(item => [item.runtimeId, item.scope, item.label])).toEqual([
    ["general", "general", "通用会话"],
    ["rt-proj", "project", "VortoCode"],
    ["rt-scratch-abcdef", "scratch", "Scratch ws1234"],
    ["rt-other", "project", "unknown-repo"],
    ["remote:r1", "project", "远端 · 服务器"],
    ["remote:h1", "general", "常驻助理 · 家里"],
  ]);
  expect(sources.find(item => item.runtimeId === "remote:h1")?.repoRoot).toBeUndefined();
});

it("the source key ignores order but changes with any identity field", () => {
  expect(runtimeInboxSourceKey([a, b])).toBe(runtimeInboxSourceKey([b, a]));
  expect(runtimeInboxSourceKey([a, b])).not.toBe(runtimeInboxSourceKey([a, { ...b, baseUrl: "http://127.0.0.1:8082" }]));
  expect(runtimeInboxSourceKey([a])).not.toBe(runtimeInboxSourceKey([{ ...a, label: "A2" }]));
});
