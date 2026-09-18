/**
 * 受保护分支的判定——与后端 `src/gateway/git_review.py:_PROTECTED_BRANCHES` 同一份清单。
 *
 * 后端才是权威（Web / TUI 也走它）；这里这份只用来提前把话说清楚：按钮为什么禁用、
 * 提交前为什么要再问一次。两边哪天要加分支名，记得一起加。
 */
export const PROTECTED_BRANCHES = ["main", "master", "develop", "development"];

export function isProtectedBranch(branch?: string | null): boolean {
  return PROTECTED_BRANCHES.includes(String(branch ?? "").trim().toLowerCase());
}
