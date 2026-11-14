# 旧命令入口迁移（P5 准备）

2026-11-13 主人确认退出面向用户的 `vc` / `vortocode` 命令行产品；日常入口转为 Desktop
与手机。内部服务、诊断命令和 Codex / Claude / OpenCode 等第三方执行器继续保留。

本步提供内部作业入口、只读依赖盘点和重新核对报告。**P5 尚未全部完成**：安装包仍保留
两个公开兼容命令，实际服务、作业与安装尚未据此迁移，不能凭本 PR 删除入口。

## 内部作业入口

`vortoone-runtime` 在 P1 的 server / im / doctor / canary 之外支持 cron、collect、stats、
digest、pipeline、heartbeat。参数沿用旧命令，调用同一实现：

```bash
vortoone-runtime digest --limit 12
vortoone-runtime cron list
vortoone-runtime pipeline list
```

这里只是入口替换：collect / stats 仍可能联网；cron run / heartbeat run / pipeline 的执行
动作仍会产生原有副作用，不能把它们当成只读检查。确认门、无人值守默认拒绝、Project
作用域、进程租约、共享执行池和清理规则保持。App 内置受限 runner 不增加这些命令。

`agent`、attach、workers、roster 等旧功能没有内部等价入口，须逐项核对用途，不能机械改名。
跨项目服务应指向固定安装位置的可执行文件，不能在不可信项目目录直接用 `python -m` 加载。

## 只读盘点与报告核对

明确选择当前运行端的服务、作业文件或小范围目录，例如：

```bash
vortoone-runtime legacy-inventory --path /path/to/assistant.service --path /path/to/cron.yaml --output /path/to/new-review.json
vortoone-runtime legacy-verify /path/to/new-review.json
```

盘点不准备运行环境，不读取 `.env`，不迁移状态目录，不执行输入命令、调用模型或改服务配置。
它支持 launchd XML/二进制 plist、systemd 的 Exec 指令、jobs 列表式 YAML、shell/crontab
中的旧入口引用。禁用的作业也参加盘点。直接调用且内部参数有效的旧命令只给出入口替换候选；
不会核实目标程序已安装，也不更改或批准尾部参数。间接脚本、shell 组合、提示词中的引用、
Python 兼容模块和没有等价入口的命令列为人工核对项。

报告省略参数值、环境值和提示词正文，仍含所选文件路径与文件摘要，应留在本机，不提交公开仓库。
`--output` 只创建新的 0600 文件，不覆盖旧报告；不指定时报告输出到标准输出。
异常只报告固定原因，不回显解析器可能携带的源文件片段。

每次最多 128 个输入/文件、单文件 256 KiB、总文件内容 4 MiB、总引用 512 条。
目录最多深入两层，遍历条目上限 512；超过限制、符号链接、不可读/未知格式或解析失败均标为
盘点不完整。该工具不扫描整个机器、不解析任意 shell、不跟进间接脚本，也不检查其它运行端；
“未发现引用”只针对声明的范围，不能当作整机无依赖。

核对报告会重新读取原输入，包括目录成员变化。内容改变时返回不一致，不能沿用旧报告：

| 命令 | 退出码 |
| --- | --- |
| legacy-inventory | 0：所选输入读取完整；2：不完整或失败 |
| legacy-verify | 0：所选输入完整且未变；1：已变；2：不完整或失败 |

摘要不是签名或授权凭据；两条命令始终返回 `retirement_authorized=false` 和
`runtime_migration_verified=false`。报告核对成功不等于服务已迁移，更不等于作业实际执行成功。

## 撤除公开入口前的剩余步骤

1. 在每个实际运行端盘点服务、旧作业、安装脚本及间接脚本，逐项审阅人工核对项。
2. 核对固定安装的内部 runner 已有对应命令；保留原参数、工作目录、权限、环境与服务设置。
   无等价入口的依赖必须先有明确去向，不能直接删掉。
3. 由主人明确批准实际迁移，再改配置、重启和验证原任务；不把只读预览当作这项授权。
4. 用真实运行证据核对服务、定时作业、结果与通知；对未到点或未执行的项目明确保留未验证。
5. 所有依赖迁移后，另一步撤除安装包装及公开 CLI 的产品入口，更新安装与兼容说明。

本步不修改任何实际主机配置，不发版、部署、执行现有作业或启用关闭的心跳。

## 行为测试

`test_runtime_jobs.py` 覆盖旧实现的参数/退出码、流水线审核身份、cron 调用、心跳作用域与
已有进程租约，以及盘点不初始化运行环境。`test_cli_migration.py` 覆盖报告脱敏、禁用作业、
不同服务格式、间接调用人工核对、不执行输入、旧安装兼容、读取边界及报告变化失效。
原 CLI 与运行装配测试继续验证兼容调用；这些离线测试不代替实际主机迁移验收。
