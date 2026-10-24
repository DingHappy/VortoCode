import type { DesktopRuntimeInbox, RuntimeInboxSnapshot } from "../types";
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
