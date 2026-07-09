# Public Release Checklist

Use this checklist before making the repository public or accepting broad
external traffic.

## Secrets And Configuration

- `.env` is ignored and not tracked.
- `.env.example` contains placeholders only.
- README examples use public endpoints or explicit user-owned gateway wording.
- No real API keys, webhook URLs with secrets, bot tokens, private hostnames, or
  personal relay domains are present in tracked files.

Suggested local checks:

```bash
git ls-files | rg '(^|/)\.env($|\.)' | rg -v '(^|/)\.env\.example$'
git grep -n -I -E 'sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|xox[baprs]-|BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY'
```

## Runtime Safety

- `vc server` defaults to loopback.
- Public exposure requires `VORTOCODE_API_TOKEN`.
- Shell/browser execution remains opt-in.
- Tokens are sent through cookies or headers, never query strings.

## CI And Runner Safety

- Workflow token permissions are minimum required for tests.
- Docs-only changes do not spend full CI minutes.
- Live/LLM tests are opt-in and skipped by default.
- Public pull requests should not run on trusted self-hosted runners with local
  credentials, private network access, or deployment keys.

## Documentation Honesty

- README "core features" reflects current behavior.
- Historical design docs are clearly marked as historical.
- Roadmap/planned features are not described as production-ready.
