# Public Release Checklist

Use this checklist before making the repository public or accepting broad
external traffic.

## Secrets And Configuration

- `.env` is ignored and not tracked.
- `.env.example` contains placeholders only.
- README examples use public endpoints or explicit user-owned gateway wording.
- If the default relay endpoint is enabled, it is an intentional public
  OpenAI-compatible service and still requires the user's own API key.
- No real API keys, webhook URLs with secrets, bot tokens, or private hostnames
  are present in tracked files.

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

## Desktop Distribution

- Never overwrite an installed development app with an unsigned bundle when it
  owns Keychain items. Run `npm run signing:doctor` and
  `npm run install:dev-signed`; the selected non-ad-hoc identity, bundle
  identifier and designated requirement must remain stable between builds.
- Treat `VORTOCODE_ALLOW_SIGNING_IDENTITY_CHANGE=1` as a one-time certificate
  migration override, not a normal build setting. Re-check Keychain access and
  the installed signature after every intentional identity rotation.
- Build the PyInstaller sidecar natively for every target triple; do not reuse a
  binary produced for another OS or architecture.
- Build from the isolated environment synchronized exactly from
  `desktop/requirements-runtime.lock`; reject global or extra site-packages.
- Resolve the target's standalone CPython only from
  `desktop/managed-python-sources.json`; verify the archive size, SHA-256,
  upstream metadata, executable architecture, license bundle and runtime-tree
  hash. Review every source change as a supply-chain update.
- Run `npm run check` and `npm run bundle:preview`; run
  `npm run release:verify -- --mode release` before publishing an artifact.
- Confirm the bundle contains `vortocode-runtime`, and that the sidecar accepts
  only the fixed local Gateway command surface.
- Review the generated third-party notices and inventory derived from the actual
  PyInstaller Analysis table; confirm their hashes match the release evidence.
- Confirm the generated notices include both the frozen Python distributions
  and all licenses shipped in the pinned standalone CPython full archive.
- Build arm64 sidecar, app and DMG natively on Apple Silicon; cross-host asset
  verification alone is not an arm64 release artifact.
- Sign and notarize macOS artifacts with release-owned credentials; verify the
  signature, notarization ticket and installer checksum before upload.

## Documentation Honesty

- README "core features" reflects current behavior.
- Historical design docs are clearly marked as historical.
- Roadmap/planned features are not described as production-ready.
