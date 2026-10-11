// 工作台的「在哪做：本机 / 常驻电脑」：同一个项目在本机和常驻电脑上各有一份，按项目名（目录名）
// 认成同一个，切换就是换到另一边的那份。常驻电脑上有哪些项目来自它的登记表（/api/resident/projects）。
import type { DesktopProjectProfile } from "../types";
import { isAssistantHost } from "./assistantHost";

export interface ResidentProject {
  name: string;
  repo_root: string;
  port: number;
}

export type WorkPlace = "local" | "resident";

/** 项目的「身份」：目录名，不分大小写。本机 ~/code/VortoCode 与服务器 ~/projects/VortoCode 是同一个。 */
export function projectKey(project: Pick<DesktopProjectProfile, "repoRoot" | "name">): string {
  return (project.repoRoot.split("/").filter(Boolean).pop() || project.name).toLowerCase();
}

const hostOf = (baseUrl: string) => {
  try { return new URL(baseUrl).hostname; } catch { return ""; }
};

/** 已登记的常驻电脑那份（与常驻电脑同一主机的远端项目）。 */
export function residentTwin(project: DesktopProjectProfile, projects: DesktopProjectProfile[], host: DesktopProjectProfile): DesktopProjectProfile | undefined {
  const key = projectKey(project);
  return projects.find((item) => item.kind === "remote" && !isAssistantHost(item)
    && hostOf(item.baseUrl) === hostOf(host.baseUrl) && projectKey(item) === key);
}

/** 本机那份。 */
export function localTwin(project: DesktopProjectProfile, projects: DesktopProjectProfile[]): DesktopProjectProfile | undefined {
  const key = projectKey(project);
  return projects.find((item) => item.kind !== "remote" && !item.missing && projectKey(item) === key);
}

/** 常驻电脑登记表里的同名项目（还没在 Desktop 登记时，用它来登记）。 */
export function residentEntry(project: DesktopProjectProfile, entries: ResidentProject[]): ResidentProject | undefined {
  const key = projectKey(project);
  return entries.find((entry) => projectKey({ repoRoot: entry.repo_root, name: entry.name }) === key);
}

/**
 * 工作台项目列表：去掉常驻助理；本机已有同名项目时，常驻电脑那份不单独列出——它通过「在哪做」切换。
 */
export function listedProjects(projects: DesktopProjectProfile[], host?: DesktopProjectProfile): DesktopProjectProfile[] {
  return projects.filter((project) => {
    if (isAssistantHost(project)) return false;
    if (!host || project.kind !== "remote" || hostOf(project.baseUrl) !== hostOf(host.baseUrl)) return true;
    return !localTwin(project, projects);
  });
}
