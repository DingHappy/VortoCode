# VortoOne Operations Guide

This public guide covers the supported local-first operating path and the security rules that apply when VortoOne is kept running. Environment-specific deployment records, private infrastructure details, credentials, and incident notes are intentionally not stored in the public repository.

## 一、常驻（macOS launchd）

The recommended macOS template is `examples/launchd/com.vortocode.serve.plist.example`.

1. Install VortoOne in a dedicated Python environment.
2. Copy the template into the user LaunchAgents directory and replace its documented placeholders.
3. Keep credentials outside the repository in a user-readable environment file or operating-system credential store.
4. Load the service and verify it with `vc doctor` before enabling optional background work.

VortoOne listens on the loopback interface by default. Keep that default unless a separately secured remote-access design has been reviewed.

### Linux systemd

For Linux, start from `examples/systemd/vortocode-server.service.example`. Run the service as an unprivileged dedicated user, keep its environment file mode at `600`, and install an OS sandbox backend before enabling autonomous command execution.

Do not commit service environment files. Do not place API keys, bot credentials, proxy credentials, or access tokens directly in unit files that may be copied into tickets or logs.

### Telegram 主人配对

Telegram Bot token 先放在运行 `vc` 的私密环境中，不要作为命令参数或提交到仓库。首次启用时：

1. 在部署机交互式终端运行 `vc im-pair telegram`，终端显示一次性配对码，有效期 10 分钟。
2. 用管理员自己的 Telegram 账号**私聊**机器人，发送终端给出的 `/bind <配对码>`。群聊和未持有码的人不能绑定。
3. 成功后，在相同的系统用户下启动 `vc im telegram`，或为尚未启用其他 IM 的 Gateway 启动 `vc server --im telegram`。启动时自动读取 `~/.vortocode/im-pairings/telegram-<bot-id>.json` 中的 owner ID。配对记录须为 `600`，目录须为 `700`。

同一个 Bot 只能有一个 `getUpdates` 消费者；配对时先停用该 Bot 的其他轮询实例。现有 `vc server` 只内嵌一个 IM 通道；已有钉钉服务不要直接切成 Telegram。Telegram 当前是单主人会话，即便额外配置 `VORTOCODE_TG_ALLOW_FROM`，其他用户也会被拒绝，以防消息进入主人的会话或回复被送到主人。管理员邀请其他用户的设计与上线门槛见 [Telegram 接入与邀请](./TELEGRAM_ACCESS.md)。

### Telegram 接收恢复与诊断

长轮询请求失败时，接收器按 1、2、4、8、16、30 秒退避，后续普通失败保持 30 秒上限。遇到 429 时，等待时间至少覆盖服务端提供的有效 `retry_after`，即使超过普通退避上限；该字段含义见 [Telegram Bot API](https://core.telegram.org/bots/api#responseparameters)。恢复后重置连续失败数，保留本进程累计失败数和最近失败类别。

认证后的 `/api/im/status` 在 Telegram 的 `polling` 字段中提供累计与连续失败数、畸形更新数、固定错误类别、HTTP/API 错误码、距失败/恢复的秒数和剩余等待秒数。错误日志仅记录固定类别、状态码和等待时间，不记录 Bot token、请求 URL、服务端描述或消息正文。类别用于缩小排查范围，不能单独证明根因；这些计数重启后归零。

无效的整批响应会重试且不推进 offset。单条畸形更新会被计数并跳过，后续有效更新继续处理；带有效更新 ID 的记录会照常推进 offset，因此这不是持久收件箱或消息必达保证。空轮询成功也属于连接活性，发送通知成功不算接收恢复。只读验收采样器仅保留上述诊断字段的白名单，断连采样点仍记为异常，恢复后也不会抹掉。

### 手机网关（vortocode-mobile）要单独发布

手机网关**不跑部署目录的代码**：它的 `PYTHONPATH` 指向固定的发布快照
`~/.local/share/vortocode-mobile/releases/<发布标识>`（环境文件 `~/.config/vortocode-mobile/environment`
里的 `PYTHONPATH` 与 `VORTOCODE_MOBILE_RELEASE`）。部署目录 `git merge --ff-only` 再重启，**不会**更新它。

部署目录更新后，在常驻电脑上运行：

```bash
cd ~/personal_project/VortoCode && scripts/mobile-release.sh --dry-run   # 先看会不会变
scripts/mobile-release.sh
```

脚本从部署目录当前提交生成新快照（`git archive` 的 `src` + 沿用当前快照里的 `web/mobile` + `RELEASE.json`），
备份并切换环境文件，重启后核对服务在运行、重启计数没涨，并执行 `scripts/mobile-route-check.py`：

- 从目标快照的 `MOBILE_SELF_AUTHENTICATED` 读取全部原生 App 方法/路径，新增接口自动进入检查；不导入 Web 应用或模型。
- 若存在 `~/.config/vortocode-https/Caddyfile`（可用 `VORTOCODE_MOBILE_CADDYFILE` 指定），切换前检查两个精确 matcher。缺路径、方法不符或使用前缀通配都会阻止发布。其它反向代理可以没有该文件，发布后的公网探测仍必须通过。
- 每个原生接口发一次本机、一次公网请求：只用固定无效会话和空正文，不使用真实手机或管理员身份。不跟随重定向、不跳过 TLS 验证、不重试；公网必须是环境文件中的 HTTPS origin。本机请求限定 HTTP 回环，并带配置的 Host / HTTPS 转发头。
- 本机必须由路由本身返回预期拒绝，公网的状态和 JSON 必须一致。Caddy 的 404、管理员中间件的 `Unauthorized`、重定向、网络/TLS 失败和意外成功都不算通过。另核对管理员时间线和内部 relay 在公网仍为 404。
- 当前 21 个原生接口最多产生 46 个请求（含 4 个应被公网入口关闭的内部路径）；每次最长 8 秒、响应最多 64 KiB，遇错立即停。`--dry-run` 只核对合同/配置，不发网络请求；内容未变的正常发布仍要核对入口，但不重启。

检查器**不自动编辑或放宽 HTTPS 白名单**。新增路径需要在经审核的入口配置中精确添加，模板见 `deploy/Caddyfile.mobile-proxy`；模板已包含当前对话、派活、时间线与恢复路径，手机正文上限为 16 KiB。路径存在检查不替代 Caddy 自身的完整 `validate`，也不证明手机登录、签名写入或设备接收已验收。

快照切换后，重启、读取重启计数或路由检查失败都恢复旧环境并重启旧手机网关；不恢复用户业务数据，不改 HTTPS 配置。独立检查可以这样运行（不切换、不重启）：

```bash
python3 scripts/mobile-route-check.py --auth-file src/web/auth.py \
  --environment-file ~/.config/vortocode-mobile/environment \
  --caddyfile ~/.config/vortocode-https/Caddyfile
```

已登录的手机无需因这项发布机制重新登录（原生会话加密落盘，重启后恢复）；真实手机登录和 UI 仍需另行验收。

### 常驻助理数据备份（加密，2026-11-02）

`~/assistant/.vortocode`（目标、长期记忆、主要聊天、手机钥匙登记、撤销记录、Codex 状态）、`~/.vortocode`、
手机网关与 HTTPS 入口的配置，每天 03:40 加密打包、推到 NAS，本地与远端都只留最近 30 份。

- **只有公钥在常驻电脑上**：在主人自己的电脑上生成密钥对，私钥只留在那里（另存一份到密码管理器）：
  `python3 scripts/assistant-backup.py keygen --private ~/.config/vortocode/backup-key.pem`，把打印出的公钥放到
  常驻电脑 `~/.config/vortocode-backup/backup.pub`。
- `~/.config/vortocode-backup/environment`（600）：`VORTOCODE_BACKUP_TARGET=<user>@<nas>:<目录>/`、
  `VORTOCODE_OPS_TOKEN=<运维心跳令牌>`。NAS 那边用一把只能 rsync 的专用密钥（authorized_keys 强制命令）。
- 安装：`deploy/vortocode-backup.user.{service,timer}` 放进 `~/.config/systemd/user/`，`systemctl --user enable --now vortocode-backup.timer`。
- 成功 / 失败都报到运维心跳（`assistant-backup`），30 小时没报就推送主人。
- 恢复：`python3 scripts/assistant-backup.py restore --private <私钥> --archive <某份 .vcbak> --into <目录>`，核对后再拷回。

## 二、开关矩阵（都是 opt-in，默认全关 = 零自主消耗）

Background capabilities are disabled unless the operator enables them explicitly:

| Capability | Public example | Default |
| --- | --- | --- |
| Scheduled jobs | `examples/cron.yaml.example` | Disabled |
| Heartbeat checklist | `examples/HEARTBEAT.md.example` | Disabled |
| Backlog claiming | `examples/BACKLOG.md.example` | Disabled |
| IM bridge | `vc server --im <channel>` | Disabled |

### Phone notification quiet hours and duplicates

Every proactive notice (scheduled jobs, heartbeat, assistant follow-ups, pipelines) goes through one notifier. It always writes the notice ledger (`/api/notices`) and broadcasts to connected Desktop clients; only the phone push is shaped:

| Variable | Example | Default | Effect |
| --- | --- | --- | --- |
| `VORTOCODE_QUIET_HOURS` | `23:00-08:00` | unset (off) | Local time, may cross midnight. Pushes during the window are held and sent as one digest within a minute after it ends. An invalid value disables the window instead of swallowing notices. |
| `VORTOCODE_NOTICE_DEDUP_MIN` | `60` | `60` | The same source + same text (whitespace ignored) reaches the phone once per window. `0` turns it off. |

Delivery probes (`vc canary`) bypass both. When one service relays notices to another (e.g. project service → resident assistant), set the variables on both.

### Model usage ledger

Every model call adds its token counts (numbers only, never prompts or replies) to `~/.vortocode/usage/daily.json` under a source label: 对话, 早晚报, Codex 判断, 任务复盘, 对话摘要, 定时任务. Processes on the same machine share the file under a lock; 60 days are kept. `VORTOCODE_USAGE_DIR` moves it.

- The morning report ends with one line for yesterday, e.g. `🔢 昨天模型用量：34 次，19.8 万 token（Codex 判断 28 次 · 15.6 万；…）`, and flags a day at 3× or more of the previous 7-day average.
- The resident assistant pushes one alert per day when today's usage passes 2,000,000 tokens and is at least 3× the 7-day average (or has no history yet), naming the largest source.

Enabling a scheduler or heartbeat does not grant extra tools. The active permission profile, sandbox policy, confirmation gates, and network policy still apply.

Heartbeat behavior follows three public rules:

- The configured checklist is treated as untrusted input.
- A reply consisting only of `HEARTBEAT_OK` (apart from surrounding whitespace) is recorded without sending a user notification. Mixed findings or quoted acknowledgements are surfaced.
- A heartbeat may report or propose work, but it cannot silently merge code or broaden its own permissions.

### Continuous runtime acceptance

Run the read-only sampler on the monitored machine, with loopback HTTP origins in
`ASSISTANT_URL` and `CODE_URL`. Authentication uses the existing private API-token
configuration; credentials are never command arguments or report contents.

```bash
vc runtime-acceptance --assistant-url "$ASSISTANT_URL" --code-url "$CODE_URL" \
  --require-im --duration 86400 --interval 60 \
  --output .vortocode/acceptance/run.jsonl
vc runtime-acceptance --report .vortocode/acceptance/run.jsonl
```

Use an OS supervisor for long runs. Omit `--require-im` for installations without
a phone bridge. Sampling reads health, queue and bridge counters, submits no tasks,
and makes no model requests. Reports contain aggregate statuses, not conversations,
addresses or credentials; existing reports cannot be overwritten.

A missing final record, interruption or insufficient sampling coverage stays
`incomplete`. The reader checks each planned time slot and all configured targets;
duplicate samples, malformed records and contradictory status flags cannot fill
coverage gaps. Summary totals alone cannot approve a run.

`--report` groups consecutive bad samples per target into incidents, with problem
counts, first/last bad observations and the next healthy observation. These are
sampling boundaries, not exact outage durations. Details retain the latest 20
incidents, with full aggregate counts. Recovery does not erase earlier unhealthy
samples: a complete run containing them remains `failed`.

`passed` describes only the sampled runtime checks. It does not prove
task completion, phone delivery, answer quality or actual model cost. Review those
separately using task, delivery and human-acceptance evidence. Samples also cannot
detect every outage between polling intervals.

## 三、日常用法（attach 优先）

Start the state-owning runtime:

```bash
vc server
```

Then attach a client:

```bash
vc agent --attach "…"
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
- keep the VortoOne service itself off the public Internet;
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
