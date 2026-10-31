# 环境变量参考

VortoCode 读取的全部 `VORTOCODE_*` 环境变量。常用项的示例与注释在仓库根目录的 `.env.example`；这里是完整清单，按用途分组。

- **默认**：不设时的行为。「—」表示不设就不启用。
- **读取位置**：相对 `src/` 的模块。改行为前先看那里的注释。
- 布尔开关一般接受 `1/true/yes/on` 与 `0/false/no/off`，具体以读取处为准。
- 少数变量还认旧前缀（如 `AUTODEV_*`），见 `src/env_compat.py`。
- `tests/unit/test_environment_reference.py` 会检查代码里读到的每个变量都列在本文件里。新增变量时请一并补上。

## 服务与安全

| 变量 | 默认 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `VORTOCODE_API_TOKEN` | — | 主 API 与 `/ws` 的管理员鉴权。手机读取使用独立授权，账号管理桥仍需此令牌。对外暴露服务前必须设为强随机值。 | `web/auth.py`、`cli.py`、`gateway/doctor.py` 等 |
| `VORTOCODE_ALLOW_INSECURE_BIND` | `0` | 没设 API 令牌却绑定非本机地址时，服务默认拒绝启动；设 `1` 显式放行（如前面有已鉴权的反向代理）。 | `web/server.py` |
| `VORTOCODE_ENABLE_SHELL` | `0` | 宿主机命令执行的唯一总开关（`sandbox/policy.py:shell_enabled`）。Web 的 `require_shell`、cron 命令作业、终端都只认它。 | `sandbox/policy.py` 等 |
| `VORTOCODE_SANDBOX` | `auto` | OS 沙箱（seatbelt / bwrap）策略。`off` 只应在可信环境里显式放开宿主机执行。 | `sandbox/policy.py`、`agents/shell.py` 等 |
| `VORTOCODE_ENV_PASSTHROUGH` | — | 逗号分隔的变量名，准许穿透到子进程（默认会被剥掉的密钥类变量）。刻意只认进程环境、不认仓库文件，避免被投毒的仓库把密钥放行。 | `sandbox/policy.py` |
| `VORTOCODE_OPS_TOKEN` | — | 运维报到端点的专用令牌。只在常驻助理（通用范围）开放，不设则端点不存在。 | `web/routers/ops_heartbeat.py` |
| `VORTOCODE_VOICE_MCP_TOKEN` | — | 语音 MCP 端点的专用令牌。只在常驻助理开放，不设则端点不存在。 | `web/routers/voice.py` |

## 手机账号与独立网关

| 变量 | 默认 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `VORTOCODE_ACCOUNT_URL` | `https://account.vortotech.com` | 服务端可信账号来源，只接受规范 HTTPS origin 或开发用 HTTP 回环地址。手机身份必须由该来源核验；不按邮箱匹配归属。 | `gateway/account_service.py` |
| `VORTOCODE_MOBILE_PUBLIC_ORIGIN` | — | 手机入口的完整 origin，包含实际公开端口，不含路径、查询或账号密码；精确用于来源检查和账号回调。不设时手机账号登录未就绪，独立网关拒绝启动。 | `gateway/mobile_browser.py`、`web/mobile_server.py`、`web/routers/account_access.py` |
| `VORTOCODE_MOBILE_STATE_ROOT` | — | 独立手机网关必填的绝对状态根目录，必须与进程当前工作目录一致。读取原助手资料、主聊天登记和目标，不启动执行内核。 | `web/mobile_server.py` |
| `VORTOCODE_MOBILE_RELEASE` | `dev` | 独立网关发布标识，限定小写字母、数字、点、下划线与连字符，用于健康检查核对；标识本身不证明源码、部署或验收完成。 | `web/mobile_server.py` |

## 模型与上下文

| 变量 | 默认 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `VORTOCODE_PROVIDER_<NAME>_BASE` / `_KEY` | — | 登记一个 OpenAI 兼容供应商 `<name>`（小写），之后可以用 `供应商:模型` 点名。想登记几家就设几组。 | `llm/providers.py` |
| `VORTOCODE_MODEL_ROLES` | — | 按角色指定模型，如 `review=provider:model`。 | `llm/providers.py` |
| `VORTOCODE_MODEL_CONTEXT_WINDOW` | 按模型目录 | 全局覆盖模型的上下文窗口（token）。 | `llm/client.py` |
| `VORTOCODE_MODEL_CONTEXT_WINDOWS` | — | 按模型名前缀分别指定窗口，优先于全局值。 | `llm/client.py` |
| `VORTOCODE_CONTEXT_WINDOW_FRACTION` | `0.5` | 自适应历史预算取窗口的比例。 | `agents/agent_loop.py` |
| `VORTOCODE_CONTEXT_BUDGET_CAP` | `200000` | 自适应历史预算的绝对上限。 | `agents/agent_loop.py` |
| `VORTOCODE_MAX_CONTEXT_TOKENS` | — | 精确钉死历史预算，完全关掉自适应（优先级最高）。 | `agents/agent_loop.py` |
| `VORTOCODE_CONTEXT_POLICY` | `auto` | 上下文策略：`auto` 按模式选择，或固定为 `compact` / `balanced` / `preserve`。 | `agents/agent_loop.py` |
| `VORTOCODE_COMPACT` | 开 | 设 `0` 关闭摘要式压缩，退回纯锚点裁剪。 | `agents/agent_loop.py` |
| `VORTOCODE_NATIVE_TOOLS` | 开 | 默认走原生 function-calling，不支持时自动回退。设 `0` 强制用提示式协议。 | `agents/agent_loop.py`、`gateway/canary.py` |
| `VORTOCODE_STREAM_USAGE` | 按端点 | 流式响应里请求 usage。服务明确支持时开启。 | `llm/client.py` |
| `VORTOCODE_VISION_MODEL` | `mimo-v2.5` | 看图委派用的模型。 | `llm/client.py` |
| `VORTOCODE_MODEL_PRICES` | — | 每百万 token 的运行方单价（JSON）。不设则费用保持未知。 | `llm/observations.py` |
| `VORTOCODE_COST_BUDGET` | — | 总费用预算，超限告警（去重）。 | `models/cost.py` |
| `VORTOCODE_USAGE_DIR` | `~/.vortocode/usage` | 每日用量台账目录（同机多进程共用，写时加锁）。 | `llm/usage_ledger.py` |

## Agent 循环与工具限额

| 变量 | 默认 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `VORTOCODE_MAX_STEPS` | `6` | 非 build 模式单段的步数上限（防空转）。 | `agents/agent_loop.py` |
| `VORTOCODE_BUILD_MAX_STEPS` | `16` | build 模式单段的步数上限；到段尾会自动续跑。 | `agents/agent_loop.py` |
| `VORTOCODE_BUILD_AUTO_CONTINUES` | `3` | build 模式到步数上限后自动续跑的次数。 | `agents/agent_loop.py` |
| `VORTOCODE_MAX_WEB_TOOL_CALLS` | `12` | 单回合联网检索次数上限，到限后收尾。 | `agents/agent_loop.py` |
| `VORTOCODE_MAX_TOOL_RESULT` | `4000` | 单个工具结果回灌给模型的字符上限。 | `agents/tools/_common.py` |
| `VORTOCODE_MAX_READ_FILE` | `6000` | `read_file` 整文件读取的字符上限。 | `agents/tools/_common.py` |
| `VORTOCODE_MAX_IMAGE_BYTES` | 5 MiB | `read_file` 读图的大小上限。 | `agents/tools/_common.py`、`agents/tools/files.py` |
| `VORTOCODE_ARTIFACT_MAX_BYTES` | 16 MiB | 发布制品的大小上限。 | `agents/tools/artifacts.py` |
| `VORTOCODE_AUTO_RECALL` | 开 | 回合开始时自动召回相关长期记忆。设 `0` 关闭。 | `gateway/agent_session.py` |
| `VORTOCODE_TURN_DIGEST` | 开 | 回合结束后在后台写工作记录摘要（助理动态用）。设 `0` 关闭。 | `gateway/turn_digest.py` |
| `VORTOCODE_SEARCH_BACKEND` | 自动 | 固定网页搜索后端：`ddg` 或 `bing`。 | `agents/web_search.py` |

## 隔离开发流水线与外部执行者

| 变量 | 默认 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `VORTOCODE_DEV_ATTEMPTS` | `2` | 每件隔离实现的最多尝试次数（含自修复），下限 1。 | `agents/dev_policy.py` |
| `VORTOCODE_DEV_PARALLEL` | `4` | 并行隔离实现时同时在跑的子任务数，下限 1。 | `agents/dev_policy.py` |
| `VORTOCODE_DEV_REVIEW` | 开 | 落分支前的对抗审查。设 `0` 关闭，省一轮 LLM。 | `agents/dev_policy.py` |
| `VORTOCODE_DEV_REVIEW_PERSPECTIVES` | 全部 | 审查视角，逗号分隔。未知名忽略，全无效时回落为全部。 | `agents/review.py` |
| `VORTOCODE_TEST_TIMEOUT` | `1200` | worktree 里跑测试的超时（秒）。 | `agents/worktree.py` |
| `VORTOCODE_TEST_WORKERS` | — | 给 pytest 加 xdist 并行，如 `auto` 或 `4`。 | `agents/worktree.py` |
| `VORTOCODE_BG_TASKS` | `2` | 后台任务池的并发上限。 | `workflow/tasks/tasks.py` |
| `VORTOCODE_EXPERIMENTAL_EXTERNAL_WORKERS` | 关 | 实验功能总开关：打开后下面的外部执行者角色表才生效。 | `agents/external_workers.py` |
| `VORTOCODE_EXTERNAL_WORKERS` | — | 角色到外部 CLI 的映射，如 `implement=codex,review=claude`。 | `agents/external_workers.py`、`agents/dev_tools.py` |
| `VORTOCODE_EXTERNAL_WORKER_TIMEOUT` | `1800` | 外部执行者单次运行的超时（秒）。 | `agents/external_workers.py` |
| `VORTOCODE_EXTERNAL_WORKER_COOLDOWN_MIN` | `30` | 外部执行者报未登录、限流或额度不足后的冷却时间（分钟）。 | `agents/external_workers.py` |
| `VORTOCODE_CODEX_BIN` / `VORTOCODE_CLAUDE_BIN` | 走 `PATH` | 外部 CLI 的路径。 | `agents/external_workers.py` |
| `VORTOCODE_WORKER_HOME` | — | 常驻模式下外部 CLI 登录用的专用 HOME 目录。 | `agents/external_workers.py`、`cli.py` |

## 浏览器、桌面控制

| 变量 | 默认 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `VORTOCODE_ENABLE_BROWSER_CONTROL` | 关 | 给 Agent 浏览器操作工具。只在 Desktop 托管的 Web 会话里生效，IM、CLI 和无人值守一律不给。 | `agents/tools/browser.py` |
| `VORTOCODE_BROWSER_PATH` | 自动查找 | 本机 Chromium 系浏览器的路径。 | `browser/cdp.py` |
| `VORTOCODE_BROWSER_BLOCKED_DOMAINS` | — | 追加禁止操作的域名（逗号分隔）。只能追加，不能删减内置名单。 | `agents/tools/browser.py` |
| `VORTOCODE_ENABLE_DESKTOP_CONTROL` | 关 | 允许控制一台桌面机器。只在有人值守的 Web/Desktop 项目会话里可用，见 `docs/DESKTOP_CONTROL.md`。 | `agents/tools/desktop.py` |
| `VORTOCODE_DESKTOP_TARGET` | — | 被控桌面：`local` 或 `ssh:<user>@<host>`。 | `agents/tools/desktop.py`、`computer/status.py` |
| `VORTOCODE_DESKTOP_APPS` | — | 被控桌面上允许启动的应用（逗号分隔）。 | `agents/tools/desktop.py` |
| `VORTOCODE_DESKTOP_DISPLAY` | `:0` | 被控桌面的显示号。 | `agents/tools/desktop.py` |

## IM（Telegram / 钉钉）与通知

| 变量 | 默认 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `VORTOCODE_IM` | — | 在服务进程内启动的 IM 渠道：`telegram` 或 `dingtalk`。 | `web/server.py`、`gateway/runtime.py` 等 |
| `VORTOCODE_IM_BINDINGS_FILE` | — | 显式启用 Project 的 N 入口只读托管，值为项目外宿主 JSON 的绝对路径。bot 定义、账号绑定、主通知入口和 IM 模型槽分开；不能与非空 `VORTOCODE_IM` 同用。配置与未覆盖门禁见 [N 入口托管](IM_MULTI_HOST.md)。 | `gateway/runtime.py`、`gateway/im_host.py` |
| `VORTOCODE_IM_ROLE` | `interactive` | `interactive` 接收并执行消息；`notify` 只推送。 | `gateway/im_service.py` |
| `VORTOCODE_IM_CONVERSATION` | `primary` | 单入口路径中，`isolated` 独立保存聊天与人设，不合入主聊天、不接主通知/跨 bot 审批；要求稳定账号身份，不能与 `notify` 同用。N 托管另由显式绑定配置控制。 | `gateway/im_service.py`、`cli.py` |
| `VORTOCODE_IM_POLICY` | `standard` | `research_read` 为项目只读研究：仅 `read_file/list_files/grep`，无写入、命令、联网、MCP 或委派；必须显式配置 Project scope、isolated、interactive，只允许配对主人。独立策略历史不导入原普通会话。不是同 runtime 多桥开关。 | `gateway/im_service.py`、`cli.py`、`agents/research_read.py` |
| `VORTOCODE_IM_ALLOW_FROM` | — | 允许对话的发送者 ID 白名单（逗号分隔，所有渠道通用）。 | `gateway/im_service.py`、`gateway/roster.py` 等 |
| `VORTOCODE_TG_ALLOW_FROM` / `VORTOCODE_DD_ALLOW_FROM` | — | 按渠道单独设的白名单。 | `gateway/roster.py`、`gateway/im_service.py` |
| `VORTOCODE_TG_TOKEN` | — | Telegram 机器人令牌。 | `gateway/im_service.py` 等 |
| `VORTOCODE_TG_OWNER_ID` | — | Telegram 主人 ID。新部署可以不设，走配对。 | `gateway/im_service.py`、`gateway/roster.py` |
| `VORTOCODE_TG_BOT_USERNAME` | 自动解析 | 机器人用户名，用于识别群聊里的 @。解析不出来时群消息一律不响应。 | `gateway/im_service.py` |
| `VORTOCODE_DD_CLIENT_ID` / `VORTOCODE_DD_CLIENT_SECRET` | — | 钉钉应用凭据。 | `gateway/im_service.py` 等 |
| `VORTOCODE_DD_OWNER_ID` | — | 钉钉主人的 staffId。 | `gateway/im_service.py`、`gateway/roster.py` |
| `VORTOCODE_DD_CARD_TEMPLATE_ID` | — | 钉钉互动卡片模板 ID（含 `.schema` 后缀）。不设则用文本消息。 | `im/dingtalk_card.py` |
| `VORTOCODE_DD_WS_HEARTBEAT` | 关 | 钉钉长连接的应用层心跳间隔（秒），如 `30`。 | `im/dingtalk.py` |
| `VORTOCODE_IM_HEARTBEAT_EVERY` | `150` | 后台任务「还活着」心跳的间隔（秒），0 或负数关闭。 | `im/bridge.py` |
| `VORTOCODE_IM_HELLO_QUIET_MIN` | `30` | 距上次打过招呼不足这么多分钟就不再打招呼。 | `im/bridge.py` |
| `VORTOCODE_IM_LOCK_DIR` | `~/.vortocode/channel-locks` | 同一机器人只允许一个进程接管的锁文件目录。 | `gateway/process_lease.py` |
| `VORTOCODE_OWNER_NOTIFY_URL` | — | 本进程不持有 IM 桥时，把通知交给同机哪个服务转发。 | `gateway/im_runtime.py`、`scheduling/assistant_watch.py` |
| `VORTOCODE_PUBLIC_BASE_URL` | — | 手机能打开的控制台地址（通知里的审批链接用它）。 | `gateway/public_url.py` |
| `VORTOCODE_QUIET_HOURS` | — | 手机通知的安静时段，如 `23:00-08:00`。时段内只记台账，结束后合并成一条推送。 | `scheduling/notices.py` |
| `VORTOCODE_NOTICE_DEDUP_MIN` | `60` | 同一来源、同样内容多少分钟内只推一次手机。0 关闭去重。 | `scheduling/notices.py` |

## 常驻助理、定时与值班

| 变量 | 默认 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `VORTOCODE_CRON` | 关 | 启动定时作业调度循环。 | `gateway/runtime.py`、`agents/tools/cron.py` 等 |
| `VORTOCODE_CRON_TOKEN_BUDGET` | 不封顶 | 定时作业的默认 token 预算（作业自己的 `budget` 优先）。 | `scheduling/cron.py` |
| `VORTOCODE_CRON_FAIL_ESCALATE` | `3` | 连续失败多少次升级告警，0 关闭。 | `scheduling/cron.py` |
| `VORTOCODE_HEARTBEAT` | 关 | 启动值班巡视（heartbeat）。 | `gateway/runtime.py`、`scheduling/scheduler.py` 等 |
| `VORTOCODE_HEARTBEAT_EVERY` | `30m` | 值班巡视的间隔。 | `scheduling/heartbeat.py`、`scheduling/assistant_watch.py` |
| `VORTOCODE_HEARTBEAT_HOURS` | 不限 | 值班的活跃时段，如 `9-23`。 | `scheduling/heartbeat.py` |
| `VORTOCODE_HEARTBEAT_CHECKLIST` | `.vortocode/HEARTBEAT.md` | 值班检查单文件。 | `scheduling/heartbeat.py` |
| `VORTOCODE_HEARTBEAT_MODEL` | 默认模型 | 值班巡视用的模型。 | `scheduling/heartbeat.py` |
| `VORTOCODE_ASSISTANT_REPORT_TIMES` | `09:00,21:00` | 助理早报、晚报的时间。 | `scheduling/assistant_watch.py` |
| `VORTOCODE_ASSISTANT_NEWS_ROOT` | — | 早报末尾附新闻：指向一个有信号采集产出的项目目录。 | `scheduling/assistant_watch.py` |
| `VORTOCODE_SELF_HEAL` | 开 | 环境预检发现可自愈问题时自动处置。设 `0` 关闭。 | `scheduling/remediation.py` |

## 中转站巡检

| 变量 | 默认 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `VORTOCODE_RELAY_URL` | 项目默认中转 | 巡检的 OpenAI 兼容中转地址。 | `scheduling/relay_duty.py` |
| `VORTOCODE_RELAY_SK` | — | 用户 key，用于端到端检查鉴权链路。 | `scheduling/relay_duty.py` |
| `VORTOCODE_RELAY_ADMIN_TOKEN` | — | 管理 token，用于读取渠道状态和当日消耗。 | `scheduling/relay_duty.py` |
| `VORTOCODE_RELAY_DAILY_QUOTA_LIMIT` | — | 当日消耗的告警阈值。 | `scheduling/relay_duty.py` |
| `VORTOCODE_RELAY_DEPLOY_SOURCE` | `relay-deploy` | 部署后向常驻助理报到时用的来源名。 | `scheduling/relay_duty.py` |

## 插件

| 变量 | 默认 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `VORTOCODE_CALENDAR_REMIND_MINUTES` | `15` | 日程开始前多少分钟提醒，0 关闭，最多 120。 | `plugins/calendar_ics.py` |
| `VORTOCODE_MAIL_NOTIFY` | 开 | 新邮件推送。设 `0` 关闭。 | `plugins/mail.py` |

## Web 与 CLI

| 变量 | 默认 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `VORTOCODE_SERVE_URL` | `http://127.0.0.1:8080` | CLI 连接的服务地址（`vc attach`、`vc doctor` 等）。 | `cli.py`、`gateway/doctor.py` |
| `VORTOCODE_WEB_BASE` | `http://127.0.0.1:8080` | 拼制品可点链接用的地址。 | `agents/tools/artifacts.py` |
| `VORTOCODE_WEB_MCP` | 关 | 网页会话首回合自动连 MCP（opt-in）。 | `web/routers/realtime.py` |
| `VORTOCODE_JSON_LOGS` | 关 | 控制台输出 JSON 结构化日志。 | `cli.py` |
| `VORTOCODE_LOG_FILE` | — | 结构化日志落盘路径。 | `cli.py` |
| `VORTOCODE_PYTHON` | 自动 | 派生 Python 子进程时用的解释器。 | `utils/python_exe.py` |

## 存储位置覆盖

默认都在用户目录下。主要给测试和多实例隔离用。

| 变量 | 作用 | 读取位置 |
| --- | --- | --- |
| `VORTOCODE_HOOK_TRUST_STORE` | 项目 Hook 信任记录文件（刻意放在仓库外，项目不能自己声明被信任）。 | `hooks/trust.py` |
| `VORTOCODE_HOOK_ISSUE_STORE` | Hook 问题记录文件。 | `workflow/workbench/hook_issues.py` |
| `VORTOCODE_CHANGE_SOURCE_STORE` | 变更来源记录文件。 | `workflow/review/change_sources.py` |
| `VORTOCODE_RESIDENT_PROJECTS` | 常驻电脑的项目登记表文件。 | `workflow/workbench/resident_projects.py` |
| `VORTOCODE_MAX_RUN_FILES` | 控制台运行记录保留条数（默认 200；0、负数或非法值按默认处理，不会清空）。 | `workflow/workbench/runs.py` |

## 内部变量：由 Desktop、沙箱或父进程注入，不要手动设置

| 变量 | 谁设置 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `VORTOCODE_DESKTOP_SIDECAR` | Desktop | 标记 runtime 由 Desktop 托管，放开本机文件等 Desktop 专属能力。 | `web/routers/realtime.py`、`agents/tools/browser.py` 等 |
| `VORTOCODE_WORKSPACE_SCOPE` | Desktop | 工作区范围：`general` / `scratch` / `project`（不设按 `project`）。 | `gateway/workspace_scope.py` |
| `VORTOCODE_SUPERVISOR_PID` | Desktop | 监护进程 pid；它退出时 runtime 随之退出，不留孤儿。 | `gateway/supervisor_watchdog.py` |
| `VORTOCODE_MODEL_CONFIGURATION_REVISION` | Desktop | 不含凭据的模型配置版本。运行时就绪后回传启动回执；不代表模型服务可用或推理成功。 | `gateway/configuration_receipt.py` |
| `VORTOCODE_CONFIGURATION_LAUNCH_ID` | Desktop | 每次启动独有的身份，与配置版本共同核对，防止旧进程响应被当作新进程的回执。 | `gateway/configuration_receipt.py` |
| `VORTOCODE_MODEL_CHOICES` | Desktop | 默认服务上允许点名的模型列表。 | `llm/routing.py` |
| `VORTOCODE_MODEL_CONTEXT_WINDOW_SOURCE` | Desktop | 窗口值的来源标记，供设置页显示。 | `llm/client.py` |
| `VORTOCODE_CODEX_REPORT_URL` / `VORTOCODE_CODEX_REPORT_TOKEN` | Desktop | 本机 Codex 观察结果的上报地址与令牌。 | `scheduling/codex_observer.py` |
| `VORTOCODE_IN_SANDBOX` | 隔离流水线 | 标记当前进程在 worktree 隔离测试里。 | `agents/worktree.py` |
