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
- Ignored local files such as `.env`, build directories and credential stores
  were scanned separately without copying their contents into reports.
- Full reachable Git history was scanned, not only the current checkout.
- Known scanner exceptions identify an exact historical commit, file and rule;
  never suppress a whole path or secret rule.

Suggested local checks:

```bash
git ls-files | rg '(^|/)\.env($|\.)' | rg -v '(^|/)\.env\.example$'
git grep -n -I -E 'sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|xox[baprs]-|BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY'
gitleaks detect --source . --log-opts='--all' --redact
```

If a real credential ever appears in a commit, revoke or rotate it first. Git
history rewriting does not make a still-valid credential safe.

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
- The workflow forces public repositories onto `ubuntu-latest` and ignores
  private runner, package-index and cache-path variables.
- Before changing visibility, unregister repository self-hosted runners and
  remove private-only Actions variables.
- Audit retained Actions logs and artifacts: they become visible when a private
  repository is made public.

## Repository History And Metadata

- Remove generated profiles, logs, databases, archives and build products from
  Git; keep matching ignore rules so they do not return.
- Review all branches, including merged automation branches, before publishing.
- Decide explicitly whether historical commit author names and email addresses
  are acceptable public metadata. Rewriting them is destructive and requires a
  coordinated force-push, so it is not part of routine cleanup.
- Confirm every bundled image, font, sample and third-party source has a public
  redistribution license and attribution where required.

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
  Among its gates, `versionsAligned` requires `desktop/package.json`, `tauri.conf.json`,
  `Cargo.toml` and `pyproject.toml` to carry the same version.
- After the app is notarized and stapled, run `npm run release:dmg`. It stages the app
  with an `/Applications` link, creates the DMG and signs it with the app's Developer ID.
  It then notarizes (profile `VORTOCODE_NOTARY_PROFILE`, default `vortocode-notary`),
  staples, runs the Gatekeeper check and prints the SHA-256. `hdiutil create` can leave
  the new image mounted, and the mount's lock makes `notarytool` hang at file open with
  no network activity. The script detaches any such mount and refuses to submit a file
  that is still held open.
- After the app is notarized and stapled, run `npm run release:updater -- <notes file>`.
  It packs the app into `VortoCode_<version>_aarch64.app.tar.gz`, signs it with the
  updater key (`VORTOCODE_UPDATER_KEY`, default `~/.vortocode/updater.key`, never in
  the repo) and writes `latest.json`. Upload all three next to the DMG; installed apps
  read `releases/latest/download/latest.json` and verify the archive with the public
  key pinned in `tauri.conf.json` before replacing themselves. Losing the private key
  means installed apps can no longer self-update; they must install a DMG signed with a
  new key by hand.
- After the release is installed on a real Mac (one-click update or DMG), with the app open,
  run `npm run check:installed`. It is read-only and checks the running app: installed
  version, signature, Gatekeeper and stapled ticket, the app and runtime processes, the
  runtime's `/api/health/quick`, stray runtimes from another path, the recovery record, and
  that `latest.json` points at this version. It then prints a four-item manual checklist
  (sidebar, a chat turn, the assistant panel tabs, the version in Settings) to click through.
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
- Internal hostnames, home-directory paths, incident details and links to
  private knowledge bases have been removed or generalized.

## GitHub Settings After Visibility Change

- Enable branch protection/rulesets and required CI checks on the public repo.
- Enable private vulnerability reporting and dependency/security alerts.
- Re-run the full secret scan against the final public head and confirm the
  published Actions run used GitHub-hosted infrastructure.
