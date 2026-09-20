// App.tsx 的 localStorage key 常量化收口（B8-④b S1，只搬家不改行为）。
// 桌面端所有持久化 key 集中在此，App.tsx 的直接读写引用这些常量而非散落的字符串字面量，
// 避免 key 拼写漂移。notifiedDecisions 的读写 + 200 条截断也一并收在这里。

export const STORAGE_KEYS = {
  baseUrl: "vortocode.desktop.baseUrl",
  sid: "vortocode.desktop.sid",
  repoRoot: "vortocode.desktop.repoRoot",
  generalSid: "vortocode.desktop.generalSid",
  scratchSid: "vortocode.desktop.scratchSid",
  notificationsEnabled: "vortocode.desktop.notificationsEnabled",
  notifiedDecisions: "vortocode.desktop.notifiedDecisions",
  projectSessionPrefix: "vortocode.desktop.projectSid:",
  // 上次停在哪个项目：重启后回到那里，而不是一律丢回通用会话（真机 2026-09-17）。
  lastProjectId: "vortocode.desktop.lastProjectId",
} as const;

// 每个项目一个会话 id，key 由项目 id 拼出前缀。
export function projectSessionKey(projectId: string): string {
  return `${STORAGE_KEYS.projectSessionPrefix}${projectId}`;
}

// 已推送过桌面通知的决策 id 只保留最近 200 条，防止 localStorage 无限增长。
const NOTIFIED_DECISIONS_LIMIT = 200;

export function loadNotifiedDecisionIds(): Set<string> {
  try {
    const payload = JSON.parse(localStorage.getItem(STORAGE_KEYS.notifiedDecisions) ?? "[]");
    return new Set(Array.isArray(payload) ? payload.filter((item): item is string => typeof item === "string").slice(-NOTIFIED_DECISIONS_LIMIT) : []);
  } catch {
    return new Set();
  }
}

export function persistNotifiedDecisionIds(ids: Set<string>): void {
  localStorage.setItem(STORAGE_KEYS.notifiedDecisions, JSON.stringify(Array.from(ids).slice(-NOTIFIED_DECISIONS_LIMIT)));
}

/** 启动时该恢复哪个项目：记过 id 且该项目仍在注册表里才恢复，否则回通用会话（返回 null）。 */
export function projectToRestore<T extends { id: string }>(
  projects: readonly T[],
  lastProjectId: string | null,
): T | null {
  const wanted = (lastProjectId ?? "").trim();
  if (!wanted) return null;
  return projects.find((project) => project.id === wanted) ?? null;
}
