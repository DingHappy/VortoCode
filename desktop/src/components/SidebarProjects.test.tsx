import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import type { DesktopProjectProfile, GatewayProcessStatus, GatewayRecoveryRecord, ProjectSessionSummary } from "../types";
import { SidebarProjects } from "./SidebarProjects";

const noop = () => {};
const local = (id: string, name: string, extra: Partial<DesktopProjectProfile> = {}): DesktopProjectProfile =>
  ({ id, name, kind: "local", repoRoot: `/work/${name}`, baseUrl: "", lastOpenedAt: 1, ...extra });
const base = {
  activeScope: "project" as const, currentRuntimeId: "rt-front", runtimeProcesses: [] as GatewayProcessStatus[],
  runtimeRecoveries: [] as GatewayRecoveryRecord[], query: "", projectSessions: [] as ProjectSessionSummary[],
  sessions: [], residentSessions: {}, remoteProjectId: null, repoRoot: "/work/api", connection: "connected" as const,
  connectionText: "已连接", activeSid: "s1",
  onSwitchScratch: noop, onSwitchProject: noop, onSwitchSession: noop, onForgetProject: noop, onBanner: noop,
};

it("lists recent projects with their sessions and only flags the ones that need a look", () => {
  const projects = [local("p1", "api"), local("p2", "web"), local("p3", "old", { missing: true })];
  const projectSessions = [{ projectId: "p1", sid: "s1", title: "修登录", updatedAt: 2 }];
  const recoveries = [{ runtimeId: "r", projectId: "p2", workspaceRoot: "", repoRoot: "/work/web", baseUrl: "",
    startedAt: 1, updatedAt: 1, status: "crashed" }] as unknown as GatewayRecoveryRecord[];
  const html = renderToStaticMarkup(<SidebarProjects {...base} projects={projects} projectSessions={projectSessions}
    runtimeRecoveries={recoveries} />);
  expect(html).toContain("api"); expect(html).toContain("修登录");
  expect(html).toContain("上次异常退出 · 点击恢复"); expect(html).toContain("目录已不存在 · 可移除");
  expect(html).not.toContain("移除 api");                 // 正在用的项目不给移除
  expect(html).toContain("移除 web");
});

it("filters by name or session title and shows background scratch workspaces", () => {
  const projects = [local("p1", "api"), local("p2", "web")];
  const projectSessions = [{ projectId: "p2", sid: "s9", title: "首页改版", updatedAt: 2 }];
  const html = renderToStaticMarkup(<SidebarProjects {...base} projects={projects} projectSessions={projectSessions}
    query="首页" activeScope="scratch"
    runtimeProcesses={[{ running: true, scope: "scratch", runtimeId: "rt-bg", workspaceId: "abcdef123", message: "" },
      { running: true, scope: "scratch", runtimeId: "rt-front", workspaceId: "front", message: "" }]} />);
  expect(html).toContain("首页改版"); expect(html).toContain("web"); expect(html).not.toContain(">api<");
  expect(html).toContain("当前任务的隔离临时工作区"); expect(html).toContain("Scratch abcdef");
  expect(html).not.toContain("Scratch front");             // 前台这个不重复列成后台
  const empty = renderToStaticMarkup(<SidebarProjects {...base} projects={[]} />);
  expect(empty).toContain("添加项目后");
});
