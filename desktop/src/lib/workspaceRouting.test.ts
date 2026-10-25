import { describe, expect, it } from "vitest";

import type { DesktopProjectProfile, DesktopRuntimeInbox, GatewayProcessStatus } from "../types";
import { inboxTarget, remoteProcessStatus } from "./workspaceRouting";

const project = (id: string, kind: "local" | "remote", repoRoot: string): DesktopProjectProfile =>
  ({ id, name: id, kind, repoRoot, baseUrl: `https://${id}.example`, lastOpenedAt: 0 });
const projects = [project("p1", "local", "/code/app"), project("home", "remote", "@assistant")];
const scratch: GatewayProcessStatus = { runtimeId: "rt-s", running: true, message: "", baseUrl: "http://127.0.0.1:9001" };
const inbox = (patch: Partial<DesktopRuntimeInbox>): DesktopRuntimeInbox =>
  ({ runtimeId: "rt", scope: "project", label: "", baseUrl: "", snapshot: null, error: "", checkedAt: 0, ...patch });
const context = { currentRuntimeId: "rt-current", connected: true, projects, runtimeProcesses: [scratch] };

describe("inbox target", () => {
  it("stays put when the entry is the connected runtime", () => {
    expect(inboxTarget(inbox({ runtimeId: "rt-current" }), context)).toEqual({ kind: "current" });
    expect(inboxTarget(inbox({ runtimeId: "rt-current", projectId: "p1" }), { ...context, connected: false }).kind).toBe("project");
  });

  it("routes the resident assistant, scopes and projects", () => {
    expect(inboxTarget(inbox({ runtimeId: "remote:home", projectId: "home", scope: "general" }), context))
      .toMatchObject({ kind: "assistant", host: { id: "home" } });
    expect(inboxTarget(inbox({ runtimeId: "general", scope: "general" }), context)).toEqual({ kind: "scope", scope: "general", runtime: undefined });
    expect(inboxTarget(inbox({ runtimeId: "rt-s", scope: "scratch" }), context)).toEqual({ kind: "scope", scope: "scratch", runtime: scratch });
    expect(inboxTarget(inbox({ repoRoot: "/code/app" }), context)).toMatchObject({ kind: "project", project: { id: "p1" } });
  });

  it("explains why a stopped scratch or a forgotten project cannot be opened", () => {
    expect(inboxTarget(inbox({ runtimeId: "rt-gone", scope: "scratch" }), context))
      .toEqual({ kind: "unavailable", message: "这个 Scratch runtime 已经停止" });
    expect(inboxTarget(inbox({ projectId: "nope", repoRoot: "/elsewhere" }), context).kind).toBe("unavailable");
  });
});

it("a remote workspace shows a server-managed placeholder process", () => {
  expect(remoteProcessStatus(projects[0], false)).toMatchObject({
    running: false, runtimeId: "remote:p1", scope: "project", repoRoot: "/code/app", message: "远端常驻服务由服务器管理" });
  expect(remoteProcessStatus(projects[1], true)).toMatchObject({
    runtimeId: "remote:home", scope: "general", repoRoot: "", message: "常驻电脑上的助理由服务器管理" });
});
