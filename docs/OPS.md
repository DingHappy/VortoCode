# 常驻运维手册（OPS）——把 VortoCode 跑成「常驻开发同事」

> 单核化（第三批 #136–#142）之后，`vc server` 是唯一状态所有者：Web/CLI/TUI 都是它的协议
> 客户端，IM 可内嵌，后台任务/定时作业/值班心跳都归它管。本手册讲怎么把它常驻起来、开关矩阵、
> 日常用法、出问题先查什么。

## 一、常驻（macOS launchd）

模板：`examples/launchd/com.vortocode.serve.plist.example`（内含安装三步）。要点：

- `WorkingDirectory` = 目标仓库根（状态所有权落在该仓库 `.vortocode/`；`.env` 也从这读）。
- **经代理出网的机器必须把 `HTTP(S)_PROXY` 写进 plist 的 EnvironmentVariables**——launchd
  不继承 shell 环境，漏了就是"聊天超时但终端里明明能通"。
- 内嵌 IM：ProgramArguments 追加 `--im` 与 `telegram`（或 `dingtalk`）两个 string；凭证 env
  （`VORTOCODE_TG_TOKEN`/`VORTOCODE_TG_OWNER_ID`）也写进 plist。缺凭证会**拒启**（fail-closed）。
- ⚠️ **`launchctl setenv` 设过的全局变量优先级高于 `.env`**——`load_dotenv` 默认不覆盖已存在的
  环境变量。真机踩过一整天的坑（2026-07-25）：`OPENAI_API_BASE` 被 `launchctl setenv` 钉在早已
  废弃的旧中转站上（该域名返回 502），`.env` 里改成什么都没用，症状是**每个后台任务都失败**而
  日志只说「无改动/出错」。查配置**先看进程实际在用什么**，别信配置文件：

  ```bash
  launchctl getenv OPENAI_API_BASE        # 有值就说明 .env 被它压住了
  ps eww -p $(pgrep -f 'src.cli server') | tr ' ' '\n' | grep '^OPENAI_'
  launchctl unsetenv OPENAI_API_BASE      # 让 .env 成为唯一真相源，然后重载 LaunchAgent
  ```

- `NO_PROXY` 只认**精确主机名/IP**，写网段（`192.168.10.0/24`）无效——httpx 不做 CIDR 匹配，
  内网地址要一个个列。漏了的地址会被塞进代理，而远端节点连不到你的私网 IP，回给你一个
  **502**（页面上的 nginx 版本是代理服务商的，不是你自己的机器，别顺着查错方向）。

不想常驻时手动起也一样：`vortocode server --port 8080`。

## 一之二、Linux 服务器部署（systemd）与远程访问

把 server 跑在局域网服务器上、任何设备浏览器远程观察/发任务——Web 与 CLI 天生支持，
不需要改代码。模板：`examples/systemd/vortocode-server.service.example`（内含安装三步）。

**服务器侧（一次性）：**

1. Python 3.11+ 建 venv，仓库根 `pip install -e '.[all]'`；
2. `apt install bubblewrap`——Linux 的命令沙箱后端（macOS 是 seatbelt）。缺它时需隔离的
   命令 fail-closed 拒跑，不会裸执行，但等于废掉 shell 类作业；
3. `/etc/vortocode/env`（600 权限）写 `VORTOCODE_API_TOKEN` 与中转站 key——**非 127.0.0.1
   监听必须设 token**，设了则全部 `/api/*` 与 `/ws` 强制鉴权；
4. 装 systemd 单元并 `enable --now`。cron/heartbeat 开关与 macOS 完全同一套 env（第二节）。

**客户端侧（哪都行）：**

- 浏览器开 `http://<服务器>:8080/agent`——对话、决策队列、Journal、runs、审计全在；
- 终端 `vc tui --attach http://<服务器>:8080` 远程附着，或 SSH 上去原生跑；
- IM 桥接照常可内嵌（`--im telegram`），重要决策主动推手机，Web 用来深看——推拉互补。

**安全边界（务必读）：**

- token 是**全权凭据**（单用户契约，见第四节）且传输是 HTTP 明文：`--host 0.0.0.0`
  只在可信内网可接受；**出内网一律走 Tailscale**（或 SSH 隧道
  `ssh -L 8080:127.0.0.1:8080 user@服务器`，此时 server 保持默认只听 127.0.0.1）。
  裸挂公网禁止，反代加 TLS 也只算及格线，不如 Tailscale 省心。
- Desktop 客户端刻意只连 127.0.0.1（文件区直读本机磁盘的 local-first 设计）；
  「Desktop 连远端工作区」是独立立项（B9-③），未落地前远程入口就是浏览器 + attach。

## 二、开关矩阵（都是 opt-in，默认全关 = 零自主消耗）

| 开关 | 效果 | 开销 |
| --- | --- | --- |
| （默认） | attach / 后台任务 / REST / WS / 通知台账 | 只在你主动用时烧 token |
| `VORTOCODE_CRON=1` | 跑 `.vortocode/cron.yaml` 定时作业 | 按作业表 |
| `VORTOCODE_HEARTBEAT=1` | 值班心跳：读 `HEARTBEAT.md`、从 `BACKLOG.md` 领活 | **周期性烧 token**，确认再开 |
| `VORTOCODE_HEARTBEAT_EVERY=30m` | 心跳间隔 | — |
| `VORTOCODE_HEARTBEAT_CHECKLIST=` | 换掉默认检查单文件（默认 `.vortocode/HEARTBEAT.md`），路径必须在工作目录内 | — |
| `--im telegram\|dingtalk` | IM 内嵌（手机收通知/发任务/按钮确认） | 忽略不计 |

配置模板（复制到目标仓库 `.vortocode/` 去掉 `.example`）：
`examples/cron.yaml.example` · `examples/HEARTBEAT.md.example` · `examples/BACKLOG.md.example`。

**值班惯例（心跳读检查单）**：检查单文件就是值班职责本身。两条约定值得知道：

- **无事静默**：检查单全过时心跳**一个字都不产出**（不通知、不写会话历史），只往 Journal 落一行
  确定性台账（结果分类 + 检查单路径与条目数，不含模型正文）。想确认"班值过了"就看当天 Journal。
- **检查单按不可信输入处理**：它虽是本地文件，但读进来即让该回合进**污点态**——免确认授权全部失效，
  检查单里写的任何指令都不会变成动作。别把"想让它自动做的事"写进检查单，那里只列**要查什么**。

**信任模型：single-token 单用户（刻意如此，不是疏漏）**。Web/REST 层只有一个共享凭据
`VORTOCODE_API_TOKEN`（不设则完全放行，配合默认只绑 `127.0.0.1`），**没有按用户/按会话的身份**。
两条推论别误当成 bug：

- 终端与运行记录**不按会话归属**：凡是过了鉴权的调用方都能驱动任意 `terminal id`。
  这不构成额外授权——拿到该 token 的人本来就能 `POST /api/terminals` 开自己的 PTY、
  `POST /api/runs` 跑任意命令。把归属挂在调用方自报的 id 上只是摆设：伪造它所需的凭据，
  正是威胁模型里假定攻击者已经拿到的那一个。
- 真正承重的是 **fail-closed**：整个命令执行面（`POST /api/runs`、`/api/terminals` 的
  **全部**端点，含 output/input/resize/stop）都过 `require_shell()`，
  `VORTOCODE_ENABLE_SHELL` 未设即 403。契约钉在 `tests/integration/test_security.py`。

**所以：暴露到 127.0.0.1 之外前必须设 `VORTOCODE_API_TOKEN`**，并且要清楚它是**全权凭据**——
没有权限分级，在 `ENABLE_SHELL=1` 时泄漏它等于把宿主机 shell 交出去。
要多人共用得先做真正的按用户鉴权，那时上面的进程级单例与这些闸都要一并重做。

## 三、日常用法（attach 优先）

```bash
vortocode doctor                       # 先验管道：git/key/中转站/serve/权限/gh/IM 一屏结论
vortocode tui --attach                 # 日常主入口：TUI 作协议客户端，回合在 serve 跑
vortocode agent --attach "问题/任务"    # 一次性问答/脚本化；-c 续 serve 侧 cli 会话
```

- serve 缺省地址 `http://127.0.0.1:8080`，非默认端口用 `$VORTOCODE_SERVE_URL` 或 `--attach URL`。
- serve 不在时 attach **自动回退进程内**（提示一行），不挡手上的活。
- 后台任务：TUI/Web/IM 发 `/task <描述>`；`GET /api/tasks` 或 IM `/tasks` 看进度；完成后人点
  开 draft PR（永远人在合并口）。
- 通知三路：`GET /api/notices`（台账，绝不丢）· Web/WS 广播 · IM 推 owner（内嵌时）。

## 四、出问题先查什么

0. **每个后台任务都失败、日志只说「无改动/出错」**：先怀疑环境而不是模型。按上面「常驻」段的
   `launchctl getenv` / `ps eww` 两条命令确认**进程实际在用的** `OPENAI_API_BASE`——`doctor` 查
   的是 `.env` 里那个，被 `launchctl setenv` 压住时它会显示绿而任务照挂。
1. `vortocode doctor`——七项自检，硬伤退出码 1。常见：中转站不可达（代理没进 launchd env）、
   serve 未起（attach 自动回退，不算硬伤）、permissions.yaml 写坏（**运行时会静默降级成空权限**，
   只有 doctor 会告诉你）。
2. serve 日志：`~/Library/Logs/vortocode-serve.log`（launchd 模板所配）。
3. 后台任务卡死/崩溃：重启 serve 会把 running 标成 interrupted，可 `dev_resume` 续跑（write-ahead
   台账，进度不丢）。
4. 「我暂停的任务哪去了」：**暂停是显式人类意图，重启不会自动唤醒它**（recover 只救 running）；
   它会一直躺在决策队列（Desktop 决策中心 / Journal）里标「后台任务已暂停」，等你点续跑或明确
   dismiss。这是拍板过的语义（B6-7 ④，2026-07-20），有回归测试钉着，不是漏。

## 四又二分之一、运营部值班：中转站巡检（B8-① 试点一）

第一条 OPC 职责：cron 每夜跑 `python -m src.gateway.relay_duty`（确定性、零 LLM），
巡检 `https://token.vortotech.com`（生产真身，2026-07-20 实测；relay 仓库文档写的
relay.dinghappy.com 已 502 弃用。`VORTOCODE_RELAY_URL` 可换目标）：

- 免鉴权：`/api/status` 存活 + **重启检测**（start_time 快照比对，正常发布重启也会报一夜、次夜自动转绿）、
  `/api/pricing` 非空（DB 读路径）。
- 配了 `VORTOCODE_RELAY_SK`：计费链路端到端探针；配了 `VORTOCODE_RELAY_ADMIN_TOKEN`：
  渠道池冷却/禁用、当日消耗（`VORTOCODE_RELAY_DAILY_QUOTA_LIMIT` 配阈值）。未配的项跳过且明说，跳过≠通过。
- 出口走 cron run lane：全绿静默（只落 Journal 台账一行）；异常 → 决策队列 `run:<id>` + 通报。
  报告只含数值与原因，凭据绝不回显。手动验收：`vortocode cron run relay_duty`。

无人值守硬化三件套（B8-②，"能跑"→"敢托付"的分界）：

- **预算封顶**：prompt 作业可配 `budget:`（或 env `VORTOCODE_CRON_TOKEN_BUDGET` 全局默认）——
  超限在**发起下一次 LLM 调用前**拦截、作业按失败升级；agent 把异常吞掉也洗不掉（tripped 兜底）。
- **连败升级**：同一作业连续失败达阈值（`VORTOCODE_CRON_FAIL_ESCALATE`，默认 3）→ 通知台账
  单独一条 🔺 条目 + 失败 run 自带「已连续失败 N 次」——单次失败会各自进决策队列，连败才是模式信号。
- **静默死亡检测**：每天 Journal 固定有 `duty`「应跑 vs 实跑」对账节——最怕的不是作业失败
  （失败会留痕），是调度器整个没醒；启用作业缺勤会点名并给排查话术（serve 在吗 / VORTOCODE_CRON=1 吗）。
  零 LLM 零新服务，晨读兜底。

## 五、dogfood 节奏建议（把摩擦变成 BACKLOG）

1. 第 1 周：只常驻 + attach 日用，CRON/HEARTBEAT 先关；遇到的摩擦随手记进 `.vortocode/BACKLOG.md`。
2. 第 2 周：开 `VORTOCODE_CRON=1` 挂 nightly evals（cron.yaml.example 里有模板，`--compare` 出
   回归退出码非零）；观察通知三路。
3. 觉得稳了再开 `VORTOCODE_HEARTBEAT=1`，让它自己从 BACKLOG 领活——从此摩擦清单自己消化自己。

## 六、CI 现状与自建 runner（2026-07-12 起；2026-07-26 更新）

**CI 已恢复运行**，跑在自建 runner 上（4 个 runner 挂在家里那台 Linux 机器，`CI_RUNNER=self-hosted`，
私有仓库不计费）。历史背景：早前因私有仓库 2000 分钟免费额度耗尽而 `gh workflow disable`
（额度耗尽的表现是 job 根本不启动、3 秒内全红，报 *"payments have failed or your spending
limit needs to be increased"*）——自建 runner 上线后该问题不复存在。

**本地门禁照旧要跑**（CI 绿不代替它，它清空了 key 保证离线确定性）：

```bash
./scripts/ci-local.sh          # ruff + mypy + 全量测试（和 CI 跑的是同三道关）
./scripts/ci-local.sh quick    # 跳过 integration/live
```

> 关掉 CI 而不给替代品 = 没有门禁。这个脚本刻意把 `OPENAI_API_KEY` 等清空，
> 和 CI 一样保证测试离线、确定性——否则本地"绿"可能只是因为你的 `.env` 里有 key。

**恢复 CI（自建 runner 免费、私有仓库不计费）**：

1. 装一台 Linux（**必须 Linux**：Windows 无 OS 沙箱后端，无人值守路径会 fail-closed）。
   装机步骤 / 发行版选择 / bubblewrap 验证见共享知识库
   `my-knowledge/docs/projects/vortocode/ops-runner-nightly-machine.md`。
2. 仓库 Settings → Actions → Runners → New self-hosted runner，注册并装成服务。
3. 设仓库变量 **`CI_RUNNER=self-hosted`**（Settings → Secrets and variables → Actions → Variables）
   —— `ci.yml` 的 `runs-on` 是变量驱动的，**不用改代码**。
4. `gh workflow enable CI`，再手动 `workflow_dispatch` 验一次。

⚠️ self-hosted runner 之所以安全，前提是仓库**私有**（公开仓库任何人都能提 fork PR 在你机器上跑
任意代码）。**将来转公开预览时，必须把 `CI_RUNNER` 变量删掉切回托管 runner**——公开仓库托管
runner 免费无限，正好也不需要自建了。

同一台机器还可兼做**夜跑机器**（cron 评测 / heartbeat 值班），但那是**另一个风险等级**：
CI 只跑离线测试、不需要任何 key；夜跑要 API key、会自主写代码。分阶段上线，别一步到位。

### CI 全红时先查 runner 宿主机的出海能力（真机事故 2026-07-25）

自建 runner 的机器在国内，拉 `actions/checkout` 要出海。**代理一断，每个 job 都死在
"Set up job" 阶段**，报错长这样：

```
Failed to download archive 'https://codeload.github.com/actions/checkout/...' after 3 attempts.
The SSL connection could not be established
```

这与你的改动**毫无关系**——job 还没开始跑任何代码。事故当天的具体原因：mihomo 的
`🚀 节点选择` 被**手动钉死在某个具体节点**上，而那个节点下线了；同订阅里另外 38 个节点都活着，
但选择器不会自己换。三行排查：

```bash
# 1. 宿主机能不能出海（国内站通、GitHub 不通 = 代理节点问题，不是网络断了）
ssh <runner-host> 'curl -s -m 8 -o /dev/null -w "%{http_code}\n" https://www.baidu.com; \
                   curl -s -m 8 -o /dev/null -w "%{http_code}\n" https://codeload.github.com'
# 2. 看当前选中的节点是不是死的（secret 在 /etc/mihomo/config.yaml，就地取用别打印）
#    GET /proxies/<group> → now 字段；GET /providers/proxies → 各节点 alive
# 3. 切回自动选择（URLTest 会 5 分钟重测、节点死了自动换，不会再单点全断）
#    PUT /proxies/<group>  body: {"name":"♻️ 自动选择"}
```

**教训**：选择器别钉死在单个节点上——钉死等于把 CI 绑在一个会静默下线的单点上。
另外当天观察：死掉的节点几乎清一色是 Hysteria2 协议，Vless/Tuic 的基本都活着。
