import type { DesktopProjectProfile, DesktopRuntimeInbox, GatewayProcessStatus, RuntimeInboxSnapshot, WorkspaceScope } from "../types";
import { isAssistantHost } from "./assistantHost";
import { errorText } from "./errorText";

export type RuntimeInboxSource = Omit<DesktopRuntimeInbox, "snapshot" | "error" | "checkedAt">;

function sameSource(item: RuntimeInboxSource, source: RuntimeInboxSource): boolean {
  return item.runtimeId === source.runtimeId && item.baseUrl === source.baseUrl
    && item.projectId === source.projectId && item.repoRoot === source.repoRoot && item.scope === source.scope
    && item.workspaceId === source.workspaceId;
}

/** 每个运行端完成就发布，慢/失联运行端不阻挡其他运行端；取消代次不发布迟到结果。 */
export async function observeRuntimeInboxes(sources: RuntimeInboxSource[],
  read: (source: RuntimeInboxSource) => Promise<RuntimeInboxSnapshot>,
  current: () => boolean, publish: (item: DesktopRuntimeInbox) => void): Promise<boolean> {
  const results = await Promise.all(sources.map(async source => {
    let item: DesktopRuntimeInbox;
    try {
      item = { ...source, snapshot: await read(source), error: "", checkedAt: Date.now() };
    } catch (error) {
      item = { ...source, snapshot: null, error: errorText(error, "runtime 暂时无法访问"), checkedAt: Date.now() };
    }
    if (current()) publish(item);
    return item.snapshot !== null;
  }));
  // 仍有可达运行端时不额外增加整轮退避；全部不可达时才退避。
  return sources.length === 0 || results.some(Boolean);
}

/** 失败保留同一运行端的历史快照，身份改变或来源移除时不沿用旧资料。 */
export function mergeRuntimeInboxes(previous: DesktopRuntimeInbox[], sources: RuntimeInboxSource[],
  observed?: DesktopRuntimeInbox): DesktopRuntimeInbox[] {
  return sources.flatMap(source => {
    const old = previous.find(item => sameSource(item, source));
    const next = observed && sameSource(observed, source) ? observed : old;
    return next ? [{ ...next, ...source, snapshot: next.snapshot ?? old?.snapshot ?? null }] : [];
  });
}

/** 收件箱要问哪些运行端：本机托管的、正在跑的 runtime，加上登记过的远端工作区与常驻助理。 */
export function runtimeInboxSources(processes: GatewayProcessStatus[], projects: DesktopProjectProfile[]): RuntimeInboxSource[] {
  return processes
    .filter((item) => item.running && item.runtimeId && item.baseUrl)
    .map((item) => {
      const scope = item.scope ?? (item.repoRoot ? "project" : item.runtimeId === "general" ? "general" : "scratch");
      const project = projects.find((candidate) => candidate.id === item.projectId || candidate.repoRoot === item.repoRoot);
      const label = scope === "general"
        ? "通用会话"
        : scope === "scratch"
          ? `Scratch ${item.workspaceId?.slice(0, 6) || item.runtimeId?.slice(-6) || ""}`.trim()
          : project?.name || item.repoRoot?.split("/").filter(Boolean).slice(-1)[0] || "项目工作区";
      return {
        runtimeId: item.runtimeId as string,
        projectId: item.projectId,
        workspaceId: item.workspaceId,
        scope,
        label,
        repoRoot: item.repoRoot,
        baseUrl: item.baseUrl as string,
      };
    }).concat(projects.filter((item) => item.kind === "remote").map((item) => ({
      runtimeId: `remote:${item.id}`, projectId: item.id, workspaceId: undefined,
      scope: (isAssistantHost(item) ? "general" : "project") as WorkspaceScope,
      label: isAssistantHost(item) ? `常驻助理 · ${item.name}` : `远端 · ${item.name}`,
      repoRoot: isAssistantHost(item) ? undefined : item.repoRoot, baseUrl: item.baseUrl,
    })));
}

/** 来源身份指纹：任何一项变了，进行中的读取就作废、重新开一轮。 */
export function runtimeInboxSourceKey(sources: RuntimeInboxSource[]): string {
  return sources.map(item => JSON.stringify([item.runtimeId, item.baseUrl, item.label,
    item.scope, item.projectId, item.workspaceId, item.repoRoot])).sort().join("|");
}
