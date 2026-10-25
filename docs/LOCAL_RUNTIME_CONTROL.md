# Desktop 本机引擎控制

设置 → 电脑与连接直接提供本机引擎启动、重新连接、重新加载和停止。General 不要求选择项目；Scratch 停止后保留原工作区身份，随后启动恢复同一工作区。停止期间切换工作区后，不把保存的旧身份套用到新目标。

常驻助手、远程桌面与本机引擎分别管理。远端会话只提供切回本机通用工作区的入口，不显示本机诊断或控制按钮，不停止服务器。模型设置仍属于这台 Mac；保存本机配置不重启常驻服务器。

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `desktop/src/hooks/useLocalRuntimeControls.ts` | 当前本机控制编排、互斥、提示、停止后恢复身份；复用原启动与连接路径 |
| `desktop/src/lib/runtimeLifecycle.ts` | 核对作用域、进程与活动快照，确认前后重新读取；不持有任务 |
| `desktop/src/lib/managedRuntimes.ts` | 原托管进程监督、令牌与代次；停止失败保留监督状态 |
| `desktop/src-tauri/src/gateway_process.rs` | 在原监督器锁内检查预期启动身份，然后停止对应进程 |
| `src/gateway/runtime_activity.py` | 在途 HTTP 写请求的内存计数；不持有任务或停止授权 |
| `src/web/routers/ops.py` | 只读聚合原执行所有者的计数，不扫描历史任务文件 |

## 活动读取合同

`GET /api/runtime-activity` 沿用 API Token 认证。General 仅精确放行这条读取路径，不开放同前缀任意路径或项目执行接口。响应不含提示词、确认正文、凭据、任务内容。

```json
{
  "version": 1,
  "ready": true,
  "launch_id": "opaque-launch-identity",
  "scope": "general",
  "workspace_root": "/canonical/workspace/root",
  "counts": {
    "turns": 0,
    "queued_inputs": 0,
    "confirmations": 0,
    "tasks": 0,
    "commands": 0,
    "terminals": 0,
    "writes": 0
  }
}
```

- `turns` 来自原 session actor，包含取消后的清理；`queued_inputs` 包含现有输入队列和优先项；`confirmations` 来自原待确认表。
- `tasks` 使用原 TaskRunner 的活动计数，包含已提交但等待并发槽的任务。
- `commands` 合并原运行进程与尚未完成的监视器；`terminals` 只数当前所有者仍存活的终端。
- `writes` 统计当前非 GET/HEAD/OPTIONS HTTP 请求，成功、异常或取消后释放；读请求不会把自身判为繁忙。

没有冻结的启动回执时返回 `ready: false`、`launch_id: null`、`counts: null`，且不为诊断初始化执行管理器。计数属于当前进程的瞬时观察，不是任务完成、验收或其他主机停止的证据。

## 控制顺序

1. 同一客户端同时只允许一个引擎操作；操作期间禁用相关入口和设置关闭。
2. 重新读取原生进程，核对 runtime ID、启动身份、作用域、工作目录和回环地址；Project 还核对仓库目录。
3. 独立读取活动接口，不合并为旧快照；五秒请求超时或迟到超时响应均拒绝。只接受版本 1、就绪且身份一致、各计数为非负安全整数的响应；任一计数非零就保留引擎。
4. 手动停止/重载经过原生确认；取消不发停止请求。确认后再次读取进程和活动，切换来源、启动身份替换、读取失败或新活动均中止。
5. 断开当前连接，使用 `stop_gateway(runtimeId, expectedLaunchId)` 停止。原生持监督器锁检查精确启动身份，拒绝旧请求停止后来替换的进程。旧调用方不传该参数时保留兼容行为。
6. 重载只启动原目标，沿用作用域和 Scratch 工作区 ID。失败后保留直接启动入口；不自动重试或强制停止。模型设置触发的重载复用相同核对，沿用已经完成的配置保存确认。

## 明确边界

这两次读取是停止前检查，**不是跨客户端的原子静默租约或任务准入冻结**。其他客户端仍可能在最后一次读取与停止之间提交新工作；原生启动身份校验只防止停错进程，不消除这段活动竞态。需要严格无人值守切换时，还需在原执行入口增加统一准入与释放合同，不能把本接口的零计数当作该能力。

本轮覆盖设置中的当前本机控制和配置触发重载。应用明确退出、后台工作区移除等既有清理路径没有改为此合同；不改变现有任务存储、执行池、远端控制或权限。

## 验证与交付

原生测试覆盖配置无变化、会员刷新与精确启动身份；Python 测试使用真实 ASGI 在途请求、TaskRunner、actor、命令监视器和终端计数，核对认证和 General 围栏；前端测试覆盖所有计数、确认取消、确认期间新活动、身份/来源切换和超时。

浏览器夹具运行真实 SettingsModal、配置 hook、本机控制 hook 和 ManagedRuntimes；原生 IPC、进程与活动返回为合成数据。它验证入口、停止/启动调用及 Scratch 恢复身份，不代表真实 macOS 进程、模型或常驻服务器验收。最新源码需重新打包安装后才会进入已安装应用，具体记录见仓库根目录 `design-qa.md`。
