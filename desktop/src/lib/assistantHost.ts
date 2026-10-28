// 助理住在哪台电脑上：默认是常驻电脑（服务器上一直开着的 runtime，合上笔记本也照常干活），
// 连不上时退回本机兜底。常驻电脑在注册表里是一条远端工作区，用约定的目录标记区分——
// 它跑的是通用范围（没有项目目录），不能当项目打开，也不出现在工作台的项目列表里。
import { invoke } from "@tauri-apps/api/core";
import type { DesktopProjectProfile } from "../types";
import { STORAGE_KEYS } from "./storage";

/** 常驻电脑在注册表里的「目录」：不是真实路径，只用来把它和同一服务器上的项目区分开。 */
export const ASSISTANT_HOST_ROOT = "@assistant";

export function isAssistantHost(project: Pick<DesktopProjectProfile, "kind" | "repoRoot">): boolean {
  return project.kind === "remote" && project.repoRoot === ASSISTANT_HOST_ROOT;
}

/** 已登记的常驻电脑（最近登记的在前，取第一台）。 */
export function findAssistantHost(projects: DesktopProjectProfile[]): DesktopProjectProfile | undefined {
  return projects.find(isAssistantHost);
}

/** 工作台里能打开的项目：去掉常驻电脑。 */
export function workProjects(projects: DesktopProjectProfile[]): DesktopProjectProfile[] {
  return projects.filter((project) => !isAssistantHost(project));
}

/** 每台电脑各有一个「主要聊天」：本机沿用原来的键，常驻电脑按登记 id 另存。 */
export function mainChatKey(hostId?: string | null): string {
  return hostId ? `${STORAGE_KEYS.mainChatSid}:${hostId}` : STORAGE_KEYS.mainChatSid;
}

/** 原因是 macOS「本地网络」拦截（Rust 侧 network_failure_reason 的那段话）：提示条据此给「重新打开 VortoCode」。 */
export function isLocalNetworkBlocked(reason: string | null | undefined): boolean {
  return Boolean(reason?.includes("本地网络"));
}

/** 退出并经系统重新打开整个 App（和从程序坞点开一样），macOS 会据此更新「本地网络」放行记录。 */
export function relaunchApp(): Promise<void> {
  return invoke("relaunch_app");
}

/**
 * 只测这台 Mac 能不能直连常驻电脑（与 WebSocket 同一条路，不走系统代理、不带 token、不切换）。
 * 通返回 null，不通返回原因——macOS 没给「本地网络」权限时，原因里会直接指出去哪开。
 */
export async function probeAssistantHost(hostId: string): Promise<string | null> {
  try {
    await invoke("remote_reachable", { projectId: hostId });
    return null;
  } catch (error) {
    return error instanceof Error ? error.message : String(error);
  }
}
