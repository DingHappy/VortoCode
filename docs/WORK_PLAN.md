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

## 2026-07-07 Batch: PR Doctor And Fix-CI Loop

Goals:

- Add a UI-neutral PR Doctor report that turns PR review comments and failing
  CI checks into a concrete action plan.
- Add `/fix-ci <ref>` as the direct TUI entry for diagnosing PR feedback.
- Add `/pr doctor <ref>` as the PR-subcommand form, without letting it fall
  through to PR creation title parsing.
- Keep diagnosis read-only first, then route to build-mode `pr_fix` only after
  inline confirmation.

Acceptance:

- PR Doctor reports pending review comments, failing CI checks, recommended
  verify profiles, and whether auto-fix is allowed.
- Auto-fix is offered only for PR heads on `vorto/*`, matching the existing
  `pr_fix` hard gate.
- `/fix-ci <ref>` and `/pr doctor <ref>` show the report before asking for a
  build-mode repair confirmation.
- Clean PR feedback reports do not show a repair confirmation.

## 2026-07-07 Batch: Failed Check Log Excerpts

Goals:

- Fetch short failed-log excerpts for GitHub Actions checks referenced by PR
  feedback.
- Keep log fetching best-effort so non-Actions checks, missing `gh`, or log
  access failures do not break PR Doctor.
- Include the same excerpts in the `pr_fix` repair prompt so CI repair has more
  context than the check name alone.

Acceptance:

- Actions URLs such as `/actions/runs/<id>/job/<id>` resolve to
  `gh run view <id> --log-failed`.
- PR Doctor prints only compact failure excerpts, not full CI logs.
- `pr_fix` includes up to a few relevant excerpts in its repair description.
- Unit tests mock `gh` and do not require network.

## 2026-07-07 Batch: Failed Check Classification

Goals:

- Classify failed CI feedback into likely test, lint, type-check, dependency,
  environment, timeout, build, or unknown failures.
- Show the classification and a concrete next action in PR Doctor.
- Pass the classification into `pr_fix` so automated repair sees the failure
  shape before reading raw excerpts.

Acceptance:

- Explicit timed-out/cancelled check states classify as timeout even when the
  check name looks like a test.
- Common log signatures such as `ruff`, `has no attribute`, and
  `ModuleNotFoundError` classify into the expected categories.
- Unknown failures still degrade to the existing log-based workflow.

## 2026-07-07 Batch: Failed Check Job Step Localization

Goals:

- Fetch failed GitHub Actions job and step names for each failed run when
  available.
- Show the job/step location next to PR Doctor log excerpts.
- Include job/step location in the `pr_fix` repair prompt.

Acceptance:

- Each failed run uses at most one extra `gh run view <id> --json jobs` call.
- Missing or unsupported job JSON does not break log excerpts or failure
  classification.
- PR Doctor can show `job > step` before the compact excerpt.

## Backlog

- GitHub and CI integration: map failure categories to repair templates such as
  "run exact pytest selector", "run ruff --fix", or "inspect dependency file".
- Session resume quality: show session summaries, branch/workdir/mode health,
  and recovery hints before resuming.
- Memory automation: propose project memory updates after successful commits or
  repeated user preferences, with explicit review before saving.
