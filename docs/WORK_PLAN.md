# VortoCode Work Plan

> Current implementation plan for the Claude Code gap-closing track. Older
> roadmap documents may contain historical route-A ideas; this file tracks the
> active TUI and developer-workflow direction.

## 2026-07-07 Batch: Review To Fix Loop

Goals:

- Add `/review --fix` so a P0/P1 diff review can become an explicit, confirmed
  build-mode repair request.
- Make `/preflight` recommend both code review and verification, not only tests
  and commit text.
- Add `/verify run <command>` as a minimal runtime/smoke verification entry with
  the existing dangerous-command guard and command confirmation.

Acceptance:

- `/review --fix` stays read-only when the review reports no P0/P1 or when LLM
  review is unavailable.
- `/review --fix` switches to build mode only after inline confirmation.
- `/preflight` shows the suggested `/review` command for workspace and staged
  scopes.
- `/verify run <command>` rejects obviously dangerous commands and requires
  confirmation before running safe runtime checks.
- Focused TUI and git-workflow tests cover the command parsing and user-visible
  reports.

## 2026-07-07 Batch: Diff Review UX And Verify Profiles

Goals:

- Add `/diff hunks` so a user can see stable H1/H2 style ids for the current
  workspace or staged diff.
- Add `/review hunk H1` and `/review --fix hunk H1` so review and repair can be
  scoped to one concrete diff hunk.
- Add verify profiles so common runtime checks do not require retyping shell
  commands. Builtins cover this repository, and `.vortocode/verify.yaml` can
  define project-local profiles.

Acceptance:

- `/diff hunks [cached] [path...]` prints hunk ids, file paths, and hunk headers.
- `/review hunk H1` sends only that hunk to the reviewer; missing ids give a
  useful error that points back to `/diff hunks`.
- `/review --fix hunk H1` keeps the same inline confirmation and build-mode gate
  as whole-diff fix.
- `/verify profiles` lists builtin and project profiles.
- `/verify <profile>` and `/verify profile <profile>` resolve to the configured
  command, then reuse the same dangerous-command guard and confirmation prompt
  as `/verify run`.

## 2026-07-07 Batch: Preflight Verify Profile Recommendations

Goals:

- Let `/preflight` recommend concrete verify profiles when the current changed
  paths match built-in repository heuristics or project `.vortocode/verify.yaml`
  path rules.
- Keep `/verify --changed` in the report as a precise test-selector fallback.
- Support project profile metadata such as `paths: [docs/**]` without changing
  the execution permission model.

Acceptance:

- TUI changes recommend `/verify tui` when TUI files change.
- Python source or tests recommend `/verify unit` when a unit profile is
  available.
- Project profiles with `paths` / `match` patterns appear in `/preflight` when
  changed files match them.
- All recommendations still run through `/verify <profile>`, which reuses the
  existing dangerous-command guard and confirmation prompt.

## Backlog

- TUI hang governance: isolate long Textual workers, add lightweight watchdog
  diagnostics, and keep test selectors focused.
- GitHub and CI integration: read PR review comments, failing checks, and
  suggested fix plans in one preflight/review surface.
- Session resume quality: show session summaries, branch/workdir/mode health,
  and recovery hints before resuming.
- Memory automation: propose project memory updates after successful commits or
  repeated user preferences, with explicit review before saving.
