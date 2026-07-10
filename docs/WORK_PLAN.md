# VortoCode Work Plan

> Canonical execution handoff for the current VortoCode implementation track.
> Read [ROADMAP.md](./ROADMAP.md) before starting a new feature so CLI, Desktop,
> and Web service work stays on the same agent runtime. The first batch marked
> `Status: Active`, when present, is the work to execute; older dated batches are
> completed history and remain as acceptance examples.

## 2026-07-10 Batch: Browser Verify V1

Status: Complete (post-review fix-up applied)

Post-review fix-up (high-effort multi-agent review found 7 confirmed issues; all fixed):

- Backward-compat regression (high): `run_runtime_check` had dropped the
  `probe = check or cmd` fallback, so legacy `serve + cmd` profiles started the
  service and returned green without ever running `cmd` or noticing a serve
  crash — a broken app could auto-create a PR. Restored `cmd` as the readiness
  probe when no `check`/`browser` is present; added two regression tests
  (cmd-probe passes; failing cmd-probe is not vacuously green).
- Readiness false-reds: each `page.goto` was hard-capped at 5s regardless of
  `ready_timeout` (heavy dev bundles whose load event exceeds 5s could never
  pass), and a warm-up HTTP >= 400 (e.g. a bundling 503) failed on the first
  attempt instead of retrying. `goto` now gets the full remaining deadline and
  warm-up HTTP errors retry until the deadline, matching connection-refused
  behavior.
- Serve early-exit in the browser path only failed on non-zero exit; now any
  early serve exit fails (matching the check path), so a stale server on the
  port cannot supply the rendered page.
- Screenshot path was reported even when no file was written (missing
  Playwright, launch failure); `screenshot_path` is now empty unless a
  non-empty file exists, so audit evidence never points at a nonexistent file.
- Cleanup: deduplicated the serve-tail folding (`_serve_tail` helper, consistent
  truncation) and the boolean-parsing sets (`_TRUTHY`/`_FALSY` shared by
  `_truthy` and `_config_bool`).
- Merge-blocker follow-up: the default `unit-core` CI deliberately omits the
  optional Playwright extra, so the launch-error test now injects the lazy
  loader instead of importing `playwright.sync_api` during the test. Both core
  Python jobs can exercise Browser Verify without installing Playwright.
- Merge-blocker follow-up: console errors from failed readiness navigations
  (notably Chromium's main-document 503 message) are retained as
  `transient_console_errors` but do not poison a later successful navigation.
  `console_errors` and `fail_on_console_error` now describe the final successful
  attempt; blocked requests and uncaught page exceptions remain sticky and red.

Validation of fix-up: the CI-equivalent core suite is green; focused browser /
runtime tests pass; opt-in live Chromium covers both direct success and a real
503-then-200 retry; a real `serve` + browser probe integration run verified
title/loopback/screenshot evidence and serve cleanup (no orphan process).

Goals:

- Extend project `.vortocode/verify.yaml` profiles with an opt-in browser probe
  that proves a served application renders in a real headless browser, not only
  that a shell health check exits successfully.
- Reuse the existing `serve` / `auto` / `ready_timeout` runtime-verification
  pipeline so browser evidence participates in the final `dev_auto` integration
  gate and a red result prevents automatic PR creation.
- Preserve a full-page screenshot plus structured page evidence that a user or
  later repair run can inspect after the temporary integration worktree is
  removed.

Profile contract:

```yaml
profiles:
  web:
    serve: npm run dev
    auto: true
    ready_timeout: 30
    browser:
      url: http://127.0.0.1:3000/
      wait_until: load            # default; networkidle is opt-in
      full_page: true
      fail_on_console_error: true # default; set false or use ignore patterns
```

- `browser.url` is required and must be a loopback URL. A redirect whose final
  URL leaves loopback fails verification.
- `browser.wait_until` defaults to `load`. `networkidle` is opt-in only, never
  the default: dev servers with HMR websockets or polling traffic may never
  reach network idle, so a `networkidle` default would time out the most common
  `npm run dev` scenario.
- `browser.fail_on_console_error` defaults to `true` but must be configurable:
  dev builds of React/Vue report framework warnings through `console.error`, so
  a hard-wired red would false-fail healthy apps. `false` disables the check;
  `browser.console_error_ignore: [<literal substring>, ...]` filters known
  noise using case-sensitive literal substring matching. V1 does not interpret
  these values as regular expressions. Uncaught page exceptions always mark
  the profile red and are not configurable.
- `browser.full_page` defaults to `true`.
- The profile's existing `ready_timeout` is reused for readiness and for
  navigation. Each phase gets at most `ready_timeout` seconds, so the
  worst-case wall time for one browser profile is roughly `2 × ready_timeout`
  plus screenshot capture; the runtime result reports elapsed time.
- `check` remains an optional readiness probe. A profile with `serve + browser`
  is valid without `check`; existing `cmd` and `serve + check` profiles keep
  their current behavior.

Dependencies:

- Playwright ships as an optional extra: `pyproject.toml` declares a `browser`
  extra so `pip install 'vortocode[browser]'` followed by
  `playwright install chromium` is the full install path. Core install stays
  Playwright-free; nothing imports Playwright at module import time.

Acceptance:

- An `auto: true` project profile with `serve + browser` starts the service in
  the integration worktree, waits for the page, navigates with headless
  Playwright, and always stops both browser and background service.
- Browser request and WebSocket routing is installed before navigation and
  blocks every non-loopback HTTP(S)/WS(S) destination. Blocked requests are
  reported and mark verification red rather than silently weakening the
  network boundary.
- Console and uncaught-page-error listeners are installed before the first
  navigation attempt so initial page-load failures cannot be missed.
- The JSON-serializable runtime result includes the requested and final URL,
  page title, screenshot path, final-attempt console errors, transient console
  errors from failed readiness attempts, and uncaught page errors.
- Screenshots are written under the **primary workspace root's**
  `.vortocode/artifacts/browser-verify/` (already a managed gitignored runtime
  area via `dev_plan.py` `_STATE_ENTRIES`), never under the temporary
  worktree's own `.vortocode/` — otherwise they vanish with worktree cleanup.
  Paths use a generated run id plus a sanitized profile id and never interpolate
  raw user-controlled path segments. A test asserts the screenshot file still
  exists after the worktree is removed.
- A main-document load failure, redirect away from loopback, uncaught page
  exception, timeout, or screenshot failure marks the profile red.
  `console.error` output marks the profile red only when
  `fail_on_console_error` is true (the default) and the message matches no
  `console_error_ignore` pattern. When possible, a screenshot is still
  preserved for diagnosis.
- Missing Playwright or browser binaries produces an explicit installation
  error and never silently skips or reports a pass. The error text includes
  the exact commands to fix it (`pip install 'vortocode[browser]'`,
  `playwright install chromium`).
- A red browser profile preserves the implementation branch, blocks automatic
  PR creation, and reports enough evidence for a later `dev_resume` or manual
  repair.
- Existing non-browser verify profiles remain backward compatible.

Validation:

- Unit tests mock the browser boundary and cover profile parsing/defaults,
  loopback, redirect, subrequest, and WebSocket enforcement; listeners attached
  before navigation; the console-error policy (default on, disabled, literal
  ignore patterns); success evidence; each failure class; safe evidence paths
  anchored to the primary workspace root; per-phase deadline enforcement; and
  unconditional process cleanup without requiring Playwright in default CI.
- An explicitly opt-in live smoke test covers headless Chromium against a local
  fixture server and verifies that the screenshot file is non-empty.
- Focused runtime-verify tests, the existing unit suite, `ruff check`,
  `python -m compileall -q src tests`, and `git diff --check` pass.

Non-goals:

- V1 does not feed screenshots back into the model or automatically retry in a
  fresh worktree. Visual self-repair is a separate follow-up after this
  verification signal is stable.
- V1 does not expose arbitrary public/private-network browser navigation or
  enable the existing Web browser endpoints by default.

## 2026-07-09 Batch: Read-Tool Noise Reduction And Resume Handoff

Goals:

- Make `list_files` / `glob` / `grep` respect repository ignore rules so
  generated caches and build outputs do not burn context or tool-call budget.
- Improve session resume handoff with working directory, last mode/branch,
  current branch/dirty health, context usage, and recent verify/commit/PR/audit
  events.
- Persist verify results into the session audit stream so a resumed session can
  show the last reproduction state instead of forcing the user to remember it.

Acceptance:

- Git repositories use `git ls-files --cached --others --exclude-standard`;
  non-git directories still honor common root `.gitignore` patterns.
- `dir` filters for `list_files` / `glob` / `grep` reject traversal and match
  path segments precisely (`src` does not include `src2`).
- `/resume` prints a compact handoff card with previous state, current health,
  context policy, and recent session-scoped audit events.

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

## 2026-07-07 Batch: Repair Templates From Failure Categories

Goals:

- Map failed-check categories to concrete repair templates in PR Doctor.
- Extract precise pytest selectors from failed log excerpts when available.
- Pass the same templates into `pr_fix` so automated repair gets a recommended
  command and repair posture.

Acceptance:

- Test failures with selectors recommend a minimal `python -m pytest -q ...`
  command.
- Ruff lint failures recommend `ruff check . --fix`.
- Dependency failures point to project dependency declarations and lock files
  rather than local-only package installation.
- Unknown failures still fall back to the raw PR feedback flow.

## 2026-07-07 Batch: Executable PR Doctor Actions

Goals:

- Mark repair templates as `verify`, `fix`, or `inspect` actions.
- Show safe executable slash actions such as `/verify run ...` in PR Doctor.
- Add `/fix-ci verify <ref>` and `/pr doctor verify <ref>` to run the first
  safe verification template after confirmation.

Acceptance:

- Safe reproduction templates run through the existing command confirmation
  gate.
- Mutating templates such as `ruff check . --fix` are shown as fix suggestions,
  not as verification actions.
- `/fix-ci verify <ref>` does not require the PR head branch to be `vorto/*`
  because it only runs local verification.

## 2026-07-07 Batch: PR Doctor Action Picker

Goals:

- Let `/fix-ci verify <ref>` choose among multiple safe verification templates
  instead of always taking the first one.
- Reuse the existing TUI list picker so filtering, arrow navigation, Enter, and
  Esc behave like model/session pickers.
- Keep command execution behind the existing command confirmation gate after a
  template is selected.

Acceptance:

- A single safe verify template still runs directly after confirmation.
- Multiple safe verify templates open a picker and run the selected template.
- Cancelling the picker does not run a command or switch modes.

## 2026-07-07 Batch: PR Doctor Verify Follow-up

Goals:

- Return structured results from the runtime verify runner.
- After `/fix-ci verify <ref>` finishes, append a PR Doctor-specific follow-up
  plan instead of leaving only generic command output.
- Distinguish "local failure reproduced" from "local verification passed".

Acceptance:

- Passing local verify explains that CI may be stale, environment-specific, or
  flaky, and suggests refreshing PR feedback before changing code.
- Failing local verify recommends `/pr-fix <ref>` when the PR head is eligible
  for automated repair.
- Cancelled or blocked verify actions do not produce a misleading repair plan.

## Backlog

- Trust foundation, split into three bounded batches: sandbox default/fallback
  policy; instruction filtering plus provenance for memory writes; and
  credential capability isolation for sessions that ingest external content.
- D4 repository memory: maintain a bounded `.vortocode/memory/MEMORY.md` index
  plus lazily loaded topic files, including reviewed update proposals after
  successful commits or repeated preferences. This depends on memory-write
  filtering from the trust foundation.
- B3 evaluation regularization: run the existing evaluation harness through
  cron, retain model/protocol baselines, and surface regressions instead of
  relying on anecdotal runs.
- C5 best-of-N isolated attempts: default to one attempt and escalate only for
  explicitly open-ended tasks or a failed first attempt. Start after Browser
  Verify V1 provides a stronger judge signal.
- IM end-to-end validation with real Telegram or DingTalk credentials. The code
  path exists; obtaining credentials and approving the live test remain user
  actions.
- Mypy adoption in small module-scoped batches; do not turn on a repository-wide
  blocking gate until the selected module is clean.
