# VortoCode Work Plan

> Canonical execution handoff for the current VortoCode implementation track.
> Read [ROADMAP.md](./ROADMAP.md) before starting a new feature so CLI, Desktop,
> and Web service work stays on the same agent runtime. The first batch marked
> `Status: Active`, when present, is the work to execute; older dated batches are
> completed history and remain as acceptance examples.

## 2026-07-11 Batch: Trust Foundation 3 — Credential Session Isolation

Status: Complete (post-review hardening applied)

Goals:

- Make external-content and credential-capable sessions structurally distinct.
  A model context must not simultaneously contain Web/MCP/IM input and tools
  that can reach host processes, authenticated remotes, or secret-bearing files.
- Apply one session capability gate before project allow/deny rules, plan/build
  escalation, hooks, and handler confirmation so none of those lower layers can
  grant a capability that the entry point withheld.
- Keep the three product entries aligned: local CLI/TUI development sessions use
  `local`; Web and IM sessions use `external`; cron/heartbeat uses the separately
  auditable `unattended` profile.

Policy contract:

- `local` grants `host_process`, `authenticated_outbound`, and
  `sensitive_files`, but denies tools marked as external content. External
  research starts in a new `external` session; it is not an in-place toggle
  because the old model history may already contain credentials.
- `external` permits Web/IM content and explicitly credential-free HTTP MCP,
  but withholds all credential-class capabilities. `unattended` currently has
  the same restrictive capability set under a distinct name so future runner
  hardening does not silently broaden it.
- `run_command`, background output, generated-code/dev runners, runtime tests,
  PR repair, and authenticated PR creation declare their required capabilities.
  Web/search and wrapped MCP tools explicitly declare `external_content`.
- MCP connection is part of the boundary, not merely its later tool call:
  `local` does not start MCP servers; `external` accepts only HTTP definitions
  with `credentialed: false` and no configured headers. Repository-controlled
  stdio commands never start under these strict profiles.
- Direct reads of `.env`, private keys, credential stores, and secret directories
  require `sensitive_files`. Bulk file listing/glob/grep excludes those paths so
  an external session cannot recover them through an unscoped repository scan.
- The profile is chosen by trusted entry-point code, not by repository files.
  `.vortocode/permissions.yaml`, an allow rule, `--yes`, build mode, or a user
  confirmation may narrow behavior but cannot widen the session capability set.
- CLI/TUI persisted history records the profile. A resume across different
  profiles does not import the old model history across the trust boundary.

Acceptance:

- Deterministic policy tests cover profile normalization/fail-closed behavior,
  sensitive path classification, local denial of external tools, and external
  denial of host/authenticated/sensitive operations before their handlers run.
- Shared-factory tests prove CLI is `local`, Web/IM are `external`, and isolated
  gateway work is `unattended`; all credential-capable tool declarations are
  identical across CLI/Web and the rich TUI equivalents.
- A project-wide allow rule and a confirmation callback returning true cannot
  make an external session run a host command or open an authenticated PR.
- MCP tests prove a local session refuses before process startup and external
  filtering excludes stdio, implicit credential state, and configured headers.
- Local CLI/TUI offers an explicit new `external` session. CLI `--continue` and
  TUI `/resume` preserve the original profile and refuse cross-profile history
  reuse. Unknown or corrupt persisted profile values restore fail-closed.
- Focused capability/factory/CLI/TUI/gateway tests, the full unit suite,
  `ruff check src tests`, `python -m compileall -q src tests`, and
  `git diff --check` pass.

Post-review security fix-up:

- Treat `.vortocode/` session state as sensitive repository data. External and
  unattended profiles deny direct reads of local CLI history and database
  files, and bulk repository reads resolve in-repository symlinks before
  deciding whether a path is safe to expose.
- `show_diff` accepts only one verified commit-ish or a two/three-dot commit
  range. Git options, pathspecs, whitespace, and control characters are
  rejected; configured fsmonitor, external diff, and textconv helpers are
  disabled so this read-only tool cannot invoke a host-side helper.
- Credential-free MCP HTTP URLs are parsed before connection. URL userinfo,
  credential-shaped query names or secret-like values, fragments, non-HTTP
  schemes, and malformed/missing hosts all fail closed.
- Regression tests reproduce the reported host-file overwrite and local
  history disclosure, then prove the target file remains unchanged and neither
  a direct path nor a symlink alias reaches the external model context.

Validation evidence:

- The full unit suite passes outside the enclosing workspace sandbox (`1312
  passed`, one pre-existing Textual input-history hang deselected).
- Integration/live passes (`44 passed, 6 opt-in skips`). `ruff check src tests`,
  isolated-cache `compileall`, and `git diff --check` are green.
- The post-review capability/Git/MCP regression set passes (`46 passed`). It
  covers local CLI history and symlink aliases, direct and range-form Git option
  injection, configured external diff helpers, URL userinfo, encoded credential
  query keys, secret-like values, fragments, and malformed endpoints.
- Focused regressions prove capability denial happens before project allow and
  handler confirmation, external TUI `@.env` expansion never reads the value,
  and CLI/TUI resume cannot move raw history across profiles.
- Review found and closed a connection-stage MCP bypass: repo-configured stdio
  previously launched before tool dispatch and inherited the full host
  environment. Tests now prove local refusal happens before startup and only
  explicitly credential-free, header-free HTTP definitions survive external
  filtering.

Non-goals:

- This batch does not add per-user cloud credential brokers, scoped short-lived
  GitHub tokens, or an automatic sanitized handoff from an external research
  session into a fresh local development session.
- Repository network-domain allowlists and OS-level sensitive-path read denial
  remain later sandbox hardening. This gate prevents VortoCode tool dispatch; it
  does not claim to replace process isolation for third-party code.

## 2026-07-10 Batch: Trust Foundation 2 — Memory Write Policy And Provenance

Status: Complete (post-review hardening applied)

Goals:

- Treat every durable-memory mutation as a real write operation: `save_memory`
  is build-only, asks for explicit confirmation, and shares one policy across
  CLI, TUI, Web, IM, and isolated gateway sessions.
- Prevent persistent prompt injection: content produced after untrusted
  Web/search/MCP input that also looks instructional is stored only as a
  reviewable proposal and is never returned by normal memory recall.
- Prevent credential persistence: secret-like values are redacted before an
  isolated quarantine record is written and cannot be promoted into durable
  memory through the normal review flow.
- Preserve provenance for every new durable memory and proposal without making
  existing SQLite databases or legacy memory rows unreadable.

Policy contract:

- A write records `source`, origin `session_id`, `tainted`, `write_method`,
  confirmation actor/state, policy decision/reasons, and policy version.
- Clean content becomes durable memory only after confirmation. Tainted but
  non-instructional facts may still be confirmed and stored with their tainted
  provenance; tainted instructional content becomes a pending proposal.
- Pending proposals are excluded from `recall_memory`. They may be listed and
  explicitly approved or rejected; approval preserves the original provenance
  and adds reviewer evidence.
- Manual recall, proposal listing, and TUI automatic recall are treated as
  untrusted data sources. Automatic recall carries an explicit boundary that
  survives attach/serve transport and re-taints the turn after reset.
- Secret-like content becomes a redacted `quarantined` proposal. The raw
  credential is never persisted, and quarantine records may be rejected but
  not approved; the user must submit a safe redacted fact instead.
- Legacy rows in `memories` remain readable. New provenance uses the existing
  `metadata` column; proposal/quarantine state lives in a separate additive
  table created with `IF NOT EXISTS`.

Acceptance:

- `save_memory` is `read_only=False`; plan mode cannot silently persist it, and
  handler-level confirmation still applies in build mode. Confirmation denial
  leaves both durable memory and proposal storage unchanged.
- CLI/Web/IM agent assembly and the rich TUI use the same policy/writer. TUI
  `/memory add`, confirmed automatic candidates, proposal listing/review, and
  the legacy Web project-memory POST route cannot bypass classification.
- Deterministic tests cover clean confirmed writes, denied writes, tainted
  factual writes, tainted instructional proposals, secret redaction and
  non-promotability, proposal approve/reject, provenance fields, recall
  exclusion, and opening a pre-policy SQLite database.
- Persistent rolling/session summaries redact secret-like values and remove
  explicit prompt-injection directives before later resume/system injection.
- Focused memory/agent-factory/TUI/Web tests, the full unit suite,
  `ruff check src tests`, `python -m compileall -q src tests`, and
  `git diff --check` pass.

Post-review security fix-up:

- Nested agents run inside a token-backed taint scope. A child turn may reset
  its own logical-turn state, but on return the caller receives the monotonic
  merge `parent_tainted OR child_tainted`; exceptions cannot erase the parent
  state.
- Single `task`, TUI delegation, automated review, and isolated worktree agents
  all use the same scope. Parallel Web/CLI and TUI delegation also returns each
  copied ContextVar's child-taint bit and merges it in the parent coroutine.
- End-to-end regressions cover `web_fetch -> task -> save_memory` in the shared
  agent factory and rich TUI. Instructional content remains excluded from
  durable recall, is stored as a pending proposal, and records `tainted=true`.
- A separate parallel regression proves Web content consumed only inside a
  child agent still taints the parent before any later memory write.

Non-goals:

- D4 repository file memory (`.vortocode/memory/MEMORY.md` plus topic files),
  index-size compaction, and semantic/vector recall are later batches.
- Credential capability/session isolation is the next D0 boundary; this batch
  prevents persistence but does not redesign which process may read secrets.
- The policy is deterministic defense-in-depth, not a claim that regex-based
  prompt-injection or secret detection can classify every adversarial input.

Validation evidence:

- Policy/factory/taint/main-agent focused tests pass (`121 passed`); TUI memory,
  automatic-recall, and persistent-summary tests pass (`11 passed`). A regression proves a mixed
  native-tool batch propagates taint immediately from `web_fetch` to a later
  `save_memory` in the same batch.
- The post-review taint/research/memory/worktree/review regression set passes
  (`133 passed`); the full rich TUI file passes (`219 passed`, one pre-existing
  Textual input-history test deselected).
- The full unit suite passes outside the enclosing development sandbox
  (`1281 passed`, the same pre-existing Textual test deselected).
- Integration/live passes (`44 passed, 6 opt-in skips`). `ruff check src tests`,
  `python -m compileall -q src tests`, and `git diff --check` are green.
- A byte-level regression verifies a detected raw API key never appears in the
  SQLite file. Another test opens a pre-policy database, reads its legacy
  memory row unchanged, and observes the additive proposal table migration.

## 2026-07-10 Batch: Trust Foundation 1 — Sandbox Policy And Fallback

Status: Complete

Post-review security fix-up:

- `src.sandbox.runner.run_code()` previously treated
  `VORTOCODE_ENABLE_SHELL=1` as sufficient host authorization after Docker was
  unavailable, bypassing `VORTOCODE_SANDBOX=required` and the unattended
  `auto` boundary used by cloud sandbox execution. It now follows Docker →
  `resolve_sandbox(require_isolation=True)` → Seatbelt/bubblewrap, and permits
  host execution only when the policy is explicitly `off` **and** the legacy
  shell capability gate is enabled. Cloud sandbox instances pass their own
  workspace into the OS sandbox write boundary.
- TUI project allow rules and the session-level “always allow commands” flag
  previously skipped the confirmation that authorizes an interactive `auto`
  host fallback. Fallback now forces a per-execution prompt that ignores both
  automatic grants. After confirmation, foreground/background commands and
  manual runtime verification require isolation unless this exact fallback was
  confirmed; this also closes a preview/execution downgrade race.
- Regression tests prove `auto`/`required` plus shell enablement cannot create a
  host marker without an OS backend, available OS isolation is selected without
  the host gate, cloud execution carries its workspace, and a TUI allow rule
  plus session blanket cannot execute before the forced fallback confirmation.
- Post-review focused sandbox/runner/shell coverage passes (`35 passed`, one
  platform skip) and TUI command/verify permission coverage passes (`22
  passed`). The full unit suite passes (`1253 passed`, one pre-existing Textual
  worker test deselected); integration/live passes (`44 passed, 6 opt-in
  skips`). A real macOS run of `run_code()` used Seatbelt, allowed its workspace
  marker, and rejected a HOME marker with `PermissionError`.

Goals:

- Replace the old boolean, macOS-only, opt-in sandbox switch with an explicit
  `auto / required / off` policy shared by interactive shell commands and every
  path that executes generated code.
- Make host fallback observable for confirmed interactive commands and fail
  closed for unattended dev/test/runtime verification unless the user has
  explicitly selected `off` for a trusted environment.
- Support both macOS Seatbelt and Linux bubblewrap without changing the current
  boundary inside the sandbox: writes are limited to the repository and temp
  directories; reads and networking remain unchanged in this batch.

Policy contract:

- `VORTOCODE_SANDBOX` defaults to `auto`.
- `auto` uses Seatbelt/bubblewrap when available. If unavailable, an interactive
  confirmed `run_command` may execute on the host only with a structured
  fallback warning; unattended execution is rejected.
- `required` rejects every command when no supported sandbox is available.
- `off` is the only unattended host-execution escape hatch and is always
  reported as explicit non-isolated execution.
- Legacy truthy values (`1/true/yes/on`) map to `required`; falsy values
  (`0/false/no/off`) map to `off`. Invalid values fail closed.

Acceptance:

- Foreground and background `run_command` results include policy, backend,
  isolated/fallback state, and a user-visible warning whenever execution is not
  isolated. The warning appears in the confirmation prompt before host
  execution, not only after the command has already run.
- `dev_isolated` / `dev_parallel` / `dev_auto` test execution, final integration
  verification, project runtime verify profiles, and the legacy tester role do
  not silently run generated code on the host. With default `auto` and no OS
  backend they return a clear red result before launching the command.
- A configured Docker pytest image remains the highest-isolation tester path;
  otherwise the same OS policy applies.
- `vc doctor` reports the effective sandbox policy/backend: available isolation
  is green, explicit `off` is a warning, and an unavailable required/unattended
  boundary is a hard failure with an install/escape-hatch hint.
- Unit tests cover normalization and invalid values, both OS argv shapes,
  interactive fallback evidence, unattended fail-closed behavior, explicit
  `off`, foreground/background execution metadata, runtime verify, worktree
  tests, Docker/OS tester selection, and doctor reporting.
- Focused tests, the full unit suite, `ruff check src tests`,
  `python -m compileall -q src tests`, and `git diff --check` pass.

Validation evidence:

- The backend check runs a cached no-op probe, not only `which`: a present but
  unusable nested `sandbox-exec` is reported unavailable instead of making
  `vc doctor` falsely green.
- A real macOS Seatbelt run outside the enclosing development sandbox allowed
  repository writes and isolated test execution while rejecting a write to the
  user's home directory. Linux bubblewrap construction is covered
  deterministically; no Linux live host was available in this batch.
- Focused sandbox/shell/worktree/runtime/doctor tests pass; the full unit suite
  passes (`1246 passed`, one pre-existing local Textual worker test deselected),
  and integration/live passes (`44 passed, 6 opt-in skips`). Ruff, compileall,
  and diff-check are green.

Non-goals:

- Network domain allowlists, sensitive-path read denial, and credential/session
  capability isolation belong to later trust-foundation batches.
- Native Windows sandboxing is not introduced; `required` fails closed there
  and `off` remains the explicit trusted-environment escape hatch.

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
