// 发版的提交必须已经在 origin/main 上。v0.2.21～0.2.23 曾直接从未合并的 PR 分支打包发布，
// main 因此落后于用户手里的版本好几天；之后从 main 开的分支或 hotfix 都会丢掉那些改动。
// 先取一次 origin/main，取不到就判不通过（fail-closed），不拿本地可能过期的引用凑数。

/**
 * run(args) 执行 git 并返回 { status }。返回 { onMain, reason }：
 * onMain 为 true 表示 commit 是 origin/main 的祖先（或就是它）。
 */
export function commitOnMain(run, commit, branch = "main") {
  if (run(["fetch", "--quiet", "origin", branch]).status !== 0) {
    return { onMain: false, reason: `could not fetch origin/${branch}` };
  }
  const ancestor = run(["merge-base", "--is-ancestor", commit, `origin/${branch}`]).status;
  if (ancestor === 0) return { onMain: true, reason: null };
  if (ancestor === 1) return { onMain: false, reason: `${commit} is not on origin/${branch}; merge the PR first and release from ${branch}` };
  return { onMain: false, reason: `could not compare ${commit} with origin/${branch}` };
}
