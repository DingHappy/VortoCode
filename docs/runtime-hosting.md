# Runtime hosting and ownership

VortoCode uses one agent factory and one TaskRunner/TaskLedger protocol. Hosting
selects the lifetime and authority of that Runtime, rather than selecting a
second agent implementation.

| Host | Lifetime | Workspace authority | IM ownership |
| --- | --- | --- | --- |
| Desktop-managed child | Starts on use; exits with Desktop or its supervisor | Explicit General, Scratch, or Project | Pins IM, cron and heartbeat off |
| Explicit `vc server` service | Remains running under its service manager | Project by default; launcher may narrow scope | Opt-in embedded bridge shares that server's runner |
| Standalone `vc im` | Runs until stopped | Same scope rules as the server | Owns its workspace and channel until stopped |

`src/gateway/runtime.py` owns recovery, dependency audit, watchdog, optional
scheduler, embedded IM startup, and cleanup. `src/web/runtime.py` supplies the
HTTP/Desktop callbacks and managers. The scheduler lives in Gateway and receives
execution and notification hooks. Gateway `RuntimeServices` owns TaskRunner construction and worker dispatch.
Development execution receives a fixed workspace root and injected tool, plan
and question factories. Web supplies session-specific questions and event
bindings; CLI heartbeat uses the same execution core without importing Web
routes. No second ledger or execution protocol is introduced.

## Workspace and channel ownership

A Runtime holds `.vortocode/runtime.lock` before recovering persisted execution
state. A second service, standalone IM process, or one-shot CLI heartbeat using the
same workspace fails startup. CLI heartbeat acquires ownership before claiming
backlog work. Recovery failures also stop startup, rather than serving with partially
recovered state. Shutdown closes admission to new tasks and drains executing workers before
releasing the workspace lock. All manager cleanup runs even if another cleanup
fails. The OS releases ownership after a process dies; the empty lock
file remains intentionally, preventing an inode replacement race.

Telegram polling/pairing and DingTalk Stream consumption also hold a per-channel lock in
`~/.vortocode/channel-locks` (`VORTOCODE_IM_LOCK_DIR` overrides the directory).
The hashed Bot/application identity remains the same across credential rotations;
credentials are never written to the lock. Distinct workspace services on the same host
cannot consume one Bot's update stream concurrently. Pairing cannot consume
updates while that Bot is already running elsewhere on that host.

These locks coordinate cooperating processes on one host. They do not implement
a distributed lease. Configure exactly one resident service per Bot across
hosts; do not point a Desktop and remote server at the same writable workspace.
Both Telegram and DingTalk use channel locks; the workspace lock covers either
standalone IM channel.

## Scope and notifications

IM inherits `VORTOCODE_WORKSPACE_SCOPE` when assembling its agent. General cannot
gain file/Shell/Git tools through an IM message. Scratch keeps its restricted
folder tools. Project-only `/task`, `/tasks`, and pipeline commands are rejected
in both General and Scratch, including direct background task submission.
Autonomous cron/heartbeat opt-in requires Project scope.

A resident server can send task completion, failure and confirmation reminders
through its embedded owner bridge. A reminder does not approve a Desktop
confirmation: the original session retains that decision. Desktop-managed
children no longer start a Bot just because Desktop inherited IM environment
variables or the runtime package has a `.env` file. These switches are explicitly
pinned off before Python loads dotenv, so removing an inherited variable cannot
accidentally reactivate a resident service. Connect Desktop to an explicitly registered resident service for
shared tasks and notifications. Independent Desktop children currently have no
cross-host notification relay to that service.

Set `VORTOCODE_IM_ROLE=notify` on a resident service to use IM only for notifications.
This mode does not construct an IM agent or register an IM task worker; incoming
messages and approval callbacks cannot submit tasks or authorize operations.
Telegram discards inbound updates before fetching attachments. It sends terminal
task results and blocked-task reminders, while ordinary task progress stays in
Desktop. Polling still checks connectivity and the heartbeat retries queued notices.
The default `interactive` role retains the existing IM control flow.

In Desktop, choose **Connect remote workspace** from the project picker or sidebar.
Register the resident service address, its actual project directory and API token.
The native layer validates the destination and stores the token in Keychain;
HTTP and WebSocket requests resolve that registered identity without sending the
credential back to the frontend. Desktop verifies the remote project and protocol
before allowing control. Existing task, queue, confirmation and change views use
that remote Runtime. Registered remote workspaces also appear in the cross-runtime
inbox while another workspace is active. Disconnecting Desktop does not stop the
service or its background tasks. Remote source browsing does not read local files
with the same path; inspect task changes through the server-backed change views.

This change does not install a local daemon, deploy a service, configure real
credentials, or prove receipt on a phone. A future local daemon can use the same
Runtime lifecycle after its explicit start/stop and reconnect UX is defined.
