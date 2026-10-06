// 右侧「预览」面板的纯数据映射：从本会话的工具活动里抽出参考来源与刚发布的制品。
import type { TurnActivity } from "../types";

export type PreviewView = "plan" | "artifacts" | "references";

export interface PreviewReference {
  id: string;
  kind: "page" | "search";
  label: string;
  /** 仅 http(s)；搜索没有可打开的地址。 */
  url?: string;
  failed: boolean;
}

function httpUrl(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  try {
    const parsed = new URL(value.trim());
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : undefined;
  } catch {
    return undefined;
  }
}

const PAGE_TOOLS = new Set(["web_fetch", "screenshot_page"]);

/** 网页抓取 / 搜索 → 参考列表（按地址或关键词去重，保持首次出现的顺序）。 */
export function previewReferences(activities: TurnActivity[]): PreviewReference[] {
  const seen = new Map<string, PreviewReference>();
  for (const activity of activities) {
    if (activity.kind !== "tool" || !activity.name) continue;
    const failed = activity.status === "failed" || activity.status === "blocked";
    let reference: PreviewReference | null = null;
    if (PAGE_TOOLS.has(activity.name)) {
      const url = httpUrl(activity.args?.url ?? activity.args?.href);
      if (url) reference = { id: `page:${url}`, kind: "page", label: url, url, failed };
    } else if (activity.name === "web_search") {
      const query = String(activity.args?.query ?? activity.args?.q ?? "").trim();
      if (query) reference = { id: `search:${query}`, kind: "search", label: query, failed };
    }
    if (!reference) continue;
    const existing = seen.get(reference.id);
    // 同一来源重试成功后不再标失败。
    if (!existing) seen.set(reference.id, reference);
    else if (existing.failed && !reference.failed) existing.failed = false;
  }
  return [...seen.values()];
}

/** publish_artifact 成功后返回的制品 id；不是这类事件返回 null。 */
export function publishedArtifactId(event: {
  name?: unknown;
  status?: unknown;
  result?: unknown;
  args?: unknown;
}): string | null {
  if (event.name !== "publish_artifact" || event.status !== "succeeded") return null;
  const fromResult = /\(id=([A-Za-z0-9._-]+),/.exec(String(event.result ?? ""))?.[1];
  if (fromResult) return fromResult;
  const args = event.args && typeof event.args === "object" ? event.args as Record<string, unknown> : {};
  const fromArgs = typeof args.id === "string" ? args.id.trim() : "";
  return /^[A-Za-z0-9._-]+$/.test(fromArgs) ? fromArgs : null;
}

/** 最近一轮（有工具调用的那一轮）的工具步骤，供「任务清单」在没有显式计划时兜底。 */
export function latestTurnSteps(activities: TurnActivity[]): TurnActivity[] {
  for (let index = activities.length - 1; index >= 0; index -= 1) {
    const rid = activities[index].rid;
    if (activities[index].kind !== "tool" || !rid) continue;
    return activities.filter((item) => item.rid === rid && item.kind === "tool");
  }
  return [];
}
