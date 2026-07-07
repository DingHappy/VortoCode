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

## Next Batches

- Diff review UX: hunk navigation, selected-hunk review/fix, and clearer
  before/after presentation.
- TUI hang governance: isolate long Textual workers, add lightweight watchdog
  diagnostics, and keep test selectors focused.
- GitHub and CI integration: read PR review comments, failing checks, and
  suggested fix plans in one preflight/review surface.
- Runtime verification: promote common project smoke commands into reusable
  profiles instead of requiring manual shell text every time.
