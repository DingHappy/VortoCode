# Security Policy

VortoCode is a public preview / dogfooding project. Treat it as a local-first
developer tool, not a hardened multi-tenant service.

## Supported Versions

Security fixes target `main`. There are no stable release branches yet.

## Reporting a Vulnerability

Please do not open a public issue for exploitable vulnerabilities, credential
leaks, or sandbox escapes. Use GitHub's private vulnerability reporting for this
repository if it is enabled, or contact the maintainer privately through their
GitHub profile.

Include:

- affected commit or version
- reproduction steps
- impact and expected boundary
- any relevant logs with secrets removed

## Operational Guidance

- Do not commit `.env`, API keys, webhook URLs with secrets, tokens, private
  certificates, or production configuration.
- Keep `vc server` bound to `127.0.0.1` unless it is behind trusted network
  controls and `VORTOCODE_API_TOKEN` is set to a strong random value.
- Do not pass API tokens in URLs. Use login cookies, `Authorization: Bearer`, or
  `X-API-Token`.
- Leave `VORTOCODE_ENABLE_SHELL` and `VORTOCODE_ENABLE_BROWSER` disabled unless
  the workspace and requested task are trusted.
- Do not run untrusted public pull requests on a self-hosted runner that has
  private network access, local credentials, or long-lived secrets.
- GitHub Actions workflows should use the minimum required token permissions and
  should not expose repository secrets to forked pull requests.
