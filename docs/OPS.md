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

不想常驻时手动起也一样：`vortocode server --port 8080`。

## 二、开关矩阵（都是 opt-in，默认全关 = 零自主消耗）

| 开关 | 效果 | 开销 |
| --- | --- | --- |
| （默认） | attach / 后台任务 / REST / WS / 通知台账 | 只在你主动用时烧 token |
| `VORTOCODE_CRON=1` | 跑 `.vortocode/cron.yaml` 定时作业 | 按作业表 |
| `VORTOCODE_HEARTBEAT=1` | 值班心跳：读 `HEARTBEAT.md`、从 `BACKLOG.md` 领活 | **周期性烧 token**，确认再开 |
| `VORTOCODE_HEARTBEAT_EVERY=30m` | 心跳间隔 | — |
| `--im telegram\|dingtalk` | IM 内嵌（手机收通知/发任务/按钮确认） | 忽略不计 |

配置模板（复制到目标仓库 `.vortocode/` 去掉 `.example`）：
`examples/cron.yaml.example` · `examples/HEARTBEAT.md.example` · `examples/BACKLOG.md.example`。

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

1. `vortocode doctor`——七项自检，硬伤退出码 1。常见：中转站不可达（代理没进 launchd env）、
   serve 未起（attach 自动回退，不算硬伤）、permissions.yaml 写坏（**运行时会静默降级成空权限**，
   只有 doctor 会告诉你）。
2. serve 日志：`~/Library/Logs/vortocode-serve.log`（launchd 模板所配）。
3. 后台任务卡死/崩溃：重启 serve 会把 running 标成 interrupted，可 `dev_resume` 续跑（write-ahead
   台账，进度不丢）。

## 五、dogfood 节奏建议（把摩擦变成 BACKLOG）

1. 第 1 周：只常驻 + attach 日用，CRON/HEARTBEAT 先关；遇到的摩擦随手记进 `.vortocode/BACKLOG.md`。
2. 第 2 周：开 `VORTOCODE_CRON=1` 挂 nightly evals（cron.yaml.example 里有模板，`--compare` 出
   回归退出码非零）；观察通知三路。
3. 觉得稳了再开 `VORTOCODE_HEARTBEAT=1`，让它自己从 BACKLOG 领活——从此摩擦清单自己消化自己。
