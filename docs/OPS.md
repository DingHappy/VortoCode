# VortoCode Operations Guide

This public guide covers the supported local-first operating path and the security rules that apply when VortoCode is kept running. Environment-specific deployment records, private infrastructure details, credentials, and incident notes are intentionally not stored in the public repository.

## 一、常驻（macOS launchd）

The recommended macOS template is `examples/launchd/com.vortocode.serve.plist.example`.

1. Install VortoCode in a dedicated Python environment.
2. Copy the template into the user LaunchAgents directory and replace its documented placeholders.
3. Keep credentials outside the repository in a user-readable environment file or operating-system credential store.
4. Load the service and verify it with `vc doctor` before enabling optional background work.

VortoCode listens on the loopback interface by default. Keep that default unless a separately secured remote-access design has been reviewed.

### Linux systemd

For Linux, start from `examples/systemd/vortocode-server.service.example`. Run the service as an unprivileged dedicated user, keep its environment file mode at `600`, and install an OS sandbox backend before enabling autonomous command execution.

Do not commit service environment files. Do not place API keys, bot credentials, proxy credentials, or access tokens directly in unit files that may be copied into tickets or logs.

## 二、开关矩阵（都是 opt-in，默认全关 = 零自主消耗）

Background capabilities are disabled unless the operator enables them explicitly:

| Capability | Public example | Default |
| --- | --- | --- |
| Scheduled jobs | `examples/cron.yaml.example` | Disabled |
| Heartbeat checklist | `examples/HEARTBEAT.md.example` | Disabled |
| Backlog claiming | `examples/BACKLOG.md.example` | Disabled |
| IM bridge | `vc server --im <channel>` | Disabled |

Enabling a scheduler or heartbeat does not grant extra tools. The active permission profile, sandbox policy, confirmation gates, and network policy still apply.

Heartbeat behavior follows three public rules:

- The configured checklist is treated as untrusted input.
- A clean result containing `HEARTBEAT_OK` is recorded without sending a user notification.
- A heartbeat may report or propose work, but it cannot silently merge code or broaden its own permissions.

## 三、日常用法（attach 优先）

Start the state-owning runtime:

```bash
vc server
```

Then attach a client:

```bash
vc tui --attach
```

The attached client can disconnect without taking ownership of the running session. Use the Web or Desktop client for reviewing diffs, pending confirmations, Goals, artifacts, and background-task results.

Before relying on background execution, confirm that:

- the repository is the intended workspace;
- the sandbox backend is available;
- write and external actions still require the expected confirmation;
- the target branch is not `main`;
- generated changes have a test and review path.

## 四、出问题先查什么

Run the deterministic diagnostic first:

```bash
vc doctor
```

Then check, in order:

1. whether the server is running and bound only to the intended interface;
2. whether required configuration names are present, without printing their values;
3. whether the model endpoint is reachable from the service process;
4. whether the sandbox backend is installed and usable;
5. whether the workspace and Git branch are correct;
6. whether the failure happened before project code ran.

Logs and diagnostic reports must redact authorization headers, cookies, query-string credentials, environment values, and message content that may contain secrets.

## 五、远程访问安全边界

The public default is local-only. If remote access is required:

- set `VORTOCODE_API_TOKEN`;
- use an authenticated encrypted tunnel or private network;
- keep the VortoCode service itself off the public Internet;
- use a least-privilege service account;
- rotate credentials after accidental disclosure;
- verify that logs do not contain tokens or request headers.

The API token is a single-user administrative credential. It must never be embedded in a URL, committed to Git, or exposed to browser history and analytics.

## 六、CI 与 Runner 排障

The public workflow uses GitHub-hosted runners. If a fork or organization chooses self-hosted runners, their installation, network, credential, and patching procedures remain the operator's responsibility and should live outside this repository.

When every CI job fails during setup, distinguish infrastructure failure from application failure:

1. inspect the failing step and runner label;
2. verify DNS, TLS, proxy, firewall, and GitHub download connectivity;
3. never print proxy or registry credentials while debugging;
4. retry only after connectivity is restored;
5. run the relevant local checks before attributing the failure to the code change.

Public examples intentionally contain placeholders only. Replace them in local configuration, never in tracked files.
