# 内部运行入口（P1）

2026-11-13 主人确认逐步退出面向用户的 `vc` / `vortocode` 命令行产品，保留内部启动、诊断
和 Codex / Claude / OpenCode 等第三方执行器。本步迁移仓库内的服务调用；公开命令包装仍在，
已有服务与旧作业不因本 PR 自动迁移或停止。

## 调用合同

安装包提供内部 `vortoone-runtime`，开发或固定部署目录可用 `python -m src.runtime_entry`。
两者支持 `server`（含原内嵌 IM 参数）、独立 `im`、`canary` 与 `doctor`；不提供 agent、attach
或交互会话产品入口。`canary` 使用离线脚本模型；`doctor` 仍可能检查已配置服务，不应把它
当作离线自检。参数解析和帮助先于准备，非法命令不创建状态目录。

`runtime_bootstrap.prepare_runtime` 复用原日志、环境名兼容和工作/家目录状态迁移顺序。
公开 CLI 与内部入口共用服务和诊断实现，CLI 日志包装仍支持原调用路径。
独立 IM 保留原凭证拒启、scope、进程租约、runner shutdown 与 adapter close。
服务本身仍走原 `start_server`、runtime 生命周期和会话装配，不新增安全判断位置。

Desktop 优先运行内置受限 Gateway；系统回退改用内部 runner。源码回退只允许构建时固定的
开发源码树，改为 `src.runtime_entry server`。本机临时引擎继续固定关闭后台通道、cron 与
heartbeat，并保留原 supervisor、token 与 workspace scope 环境。App 内置
`desktop/runtime_entry.py` 仍只接受原本机命令，权限不因通用内部入口增加而扩大。

## 迁移范围

- Desktop 的系统回退与固定开发目录回退。
- 仓库内四份 systemd 服务示例的实际启动命令。
- 部署脚本的 canary / doctor 调用。

跨项目的服务示例使用安装生成的内部可执行入口，保持从固定安装位置加载包，不能改成在
不可信项目目录执行 `python -m`。实际主机配置、已安装包与旧定时作业本轮未盘点或修改；
删除公开 CLI 前必须确认它们的依赖并显式迁移。内部 runner 并不是第三方执行器适配器，
原登录、原生权限与执行合同保持。

## 验证

`tests/unit/test_runtime_entry.py` 覆盖分发前准备、参数拒绝、诊断退出码、与 CLI 共用服务、
导入不加载公开 CLI、状态准备顺序，以及 IM 拒启/失败时的租约与清理。
Rust 行为测试核对系统内部 runner、源码模块的固定参数和原进程环境。
既有日志、所有权、CLI、三端安全与 Desktop 受限入口测试继续运行。

`runtime_entry.py` 是与 `cli.py` 同层的显式进程装配入口，分层契约只增加这一个入口例外；
业务层和安全核心仍不得导入前端，不以移动入口为由放宽权限。
门禁、构建与实际服务验收分别记录；本步不发版、部署或改变任何主机上的配置。
