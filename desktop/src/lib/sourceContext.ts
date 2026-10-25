// 源码引用（随下一条消息发给 Agent 的文件/行区间）与源码视图的行选择。纯函数，无状态。
import type { ContextItem, SourceSelection } from "../types";

/** 单轮最多带几个源码引用。 */
export const MAX_CONTEXT_ITEMS = 8;
/** 行选择最多从锚点往外扩多少行（含锚点共 500 行以内）。 */
const MAX_SELECTION_SPAN = 499;

export function contextItemKey(item: ContextItem): string {
  return `${item.path}:${item.startLine ?? "*"}:${item.endLine ?? "*"}`;
}

export function contextItemLabel(item: ContextItem): string {
  return item.startLine && item.endLine
    ? `${item.path}:L${item.startLine}–L${item.endLine}`
    : item.path;
}

/**
 * 加入一个引用。已有同一引用就不变；整文件引用顶掉同文件的所有引用，行区间只顶掉同文件的整文件引用。
 * 加满了返回 full，原列表不变。
 */
export function addContextItem(previous: ContextItem[], item: ContextItem): { items: ContextItem[]; full: boolean } {
  const key = contextItemKey(item);
  if (previous.some((candidate) => contextItemKey(candidate) === key)) return { items: previous, full: false };
  const next = item.startLine
    ? previous.filter((candidate) => candidate.path !== item.path || candidate.startLine)
    : previous.filter((candidate) => candidate.path !== item.path);
  if (next.length >= MAX_CONTEXT_ITEMS) return { items: previous, full: true };
  return { items: [...next, item], full: false };
}

/** 点一行：再点同一行取消；按住 Shift（extend）从锚点扩展，跨度有上限。 */
export function selectSourceLine(previous: SourceSelection | null, line: number, extend: boolean): SourceSelection | null {
  if (!extend && previous?.start === line && previous.end === line) return null;
  if (!extend || !previous) return { anchor: line, start: line, end: line };
  const distance = Math.max(-MAX_SELECTION_SPAN, Math.min(MAX_SELECTION_SPAN, line - previous.anchor));
  const boundedLine = previous.anchor + distance;
  return {
    anchor: previous.anchor,
    start: Math.min(previous.anchor, boundedLine),
    end: Math.max(previous.anchor, boundedLine),
  };
}

/** 文件列表里不显示 VortoCode 自己的 .vortocode/ 目录；查询按路径子串、忽略大小写。 */
export function visibleWorkspaceFiles(files: string[], query: string): string[] {
  const needle = query.trim().toLowerCase();
  const userFiles = files.filter((path) => path !== ".vortocode" && !path.startsWith(".vortocode/"));
  return needle ? userFiles.filter((path) => path.toLowerCase().includes(needle)) : userFiles;
}
