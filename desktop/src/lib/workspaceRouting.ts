// 切到某个工作区之前的判断：收件箱里的一条在哪台 runtime 上、该怎么过去；连远端时进程栏显示什么。纯函数。
import type { DesktopProjectProfile, DesktopRuntimeInbox, GatewayProcessStatus } from "../types";
import { isAssistantHost } from "./assistantHost";
import { EMPTY_PROCESS } from "./runtimeSupervision";

export type InboxTarget =
  | { kind: "current" }
  | { kind: "assistant"; host: DesktopProjectProfile }
  | { kind: "scope"; scope: "general" | "scratch"; runtime?: GatewayProcessStatus }
  | { kind: "project"; project: DesktopProjectProfile }
  | { kind: "unavailable"; message: string };

export function inboxTarget(runtimeInbox: DesktopRuntimeInbox, { currentRuntimeId, connected, projects, runtimeProcesses }: {
  currentRuntimeId: string | undefined;
  connected: boolean;
  projects: DesktopProjectProfile[];
  runtimeProcesses: GatewayProcessStatus[];
}): InboxTarget {
  if (runtimeInbox.runtimeId === currentRuntimeId && connected) return { kind: "current" };
  const host = projects.find((item) => item.id === runtimeInbox.projectId && isAssistantHost(item));
  if (host) return { kind: "assistant", host };
  if (runtimeInbox.scope === "general") {
    return { kind: "scope", scope: "general", runtime: runtimeProcesses.find((item) => item.runtimeId === runtimeInbox.runtimeId) };
  }
  if (runtimeInbox.scope === "scratch") {
    const runtime = runtimeProcesses.find((item) => item.runtimeId === runtimeInbox.runtimeId);
    return runtime ? { kind: "scope", scope: "scratch", runtime } : { kind: "unavailable", message: "这个 Scratch runtime 已经停止" };
  }
  const project = projects.find((item) => item.id === runtimeInbox.projectId || item.repoRoot === runtimeInbox.repoRoot);
  return project ? { kind: "project", project } : { kind: "unavailable", message: "项目已不在最近项目中，无法从收件箱切换" };
}

/** 远端工作区 / 常驻助理不是 Desktop 拉起的进程：进程栏显示一条「由服务器管理」的占位状态。 */
export function remoteProcessStatus(profile: DesktopProjectProfile, assistant: boolean): GatewayProcessStatus {
  return assistant
    ? { ...EMPTY_PROCESS, runtimeId: `remote:${profile.id}`, projectId: profile.id,
        scope: "general", repoRoot: "", baseUrl: profile.baseUrl, message: "常驻电脑上的助理由服务器管理" }
    : { ...EMPTY_PROCESS, runtimeId: `remote:${profile.id}`, projectId: profile.id,
        scope: "project", repoRoot: profile.repoRoot, baseUrl: profile.baseUrl, message: "远端常驻服务由服务器管理" };
}
