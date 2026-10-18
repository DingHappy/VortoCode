# 桌面操作（实验功能，默认关闭）

让 Agent 看一个 Linux X11 桌面，并在你逐次确认后点击、输入、按键、启动应用。实现在
`src/agents/tools/desktop.py`，装配在 `src/gateway/agent_session.py`。

## 什么时候可用

同时满足才会装配这组工具：

- 主进程环境设了 `VORTOCODE_ENABLE_DESKTOP_CONTROL=1`（默认不设 = 关闭）；
- 会话是 Web / Desktop（`kind=web`），且问得到人（`can_ask_human`）；
- 会话档位是 `local`（项目会话）。IM、无头 CLI、无人值守（cron / heartbeat）、`external` 档位一律不给。

## 工具

| 工具 | 类型 | 说明 |
| --- | --- | --- |
| `desktop_screenshot` | 只读 | 截屏，图片附在本回合供模型查看；屏幕内容按不可信外部内容处理（本回合打污点） |
| `desktop_windows` | 只读 | 可见窗口列表（编号、位置大小、标题）和当前活动窗口 |
| `desktop_click` | 写 | 点击坐标，可右键、双击 |
| `desktop_type` | 写 | 在当前焦点处输入文字（最多 2000 字符） |
| `desktop_key` | 写 | 按键或组合键（`Return`、`ctrl+l`、`alt+F4`…） |
| `desktop_open` | 写 | 启动 `VORTOCODE_DESKTOP_APPS` 名单里的应用 |

写操作每次都过确认门，归为 `interact` 类，**任何授权档位都不免**。确认文案写明目标桌面、当前活动窗口和具体动作；
完成后返回一张新截屏。

## 安全边界

- 看起来像密码或密钥的文字不输入（复用记忆写入策略的凭据识别）；需要登录时由人在桌面上自己操作。
- 按键名只接受 `[A-Za-z0-9_]` 与 `+` 组合；应用名只接受名单里的合法命令名。
- 命令一律按参数数组执行；要输入的文字走 stdin，不进命令行；经 SSH 时每个参数单独转义。
- 启动应用前先确认目标机器装了它，启动后检查进程在不在——不会对一个没起来的应用报「已完成」。
- 文字会进入**当前有焦点的窗口**：确认前看一眼确认框里的「当前窗口」。

## 配置

在 Desktop 里：设置 → 集成 →「桌面操作（实验）」，填目标桌面（`ssh:用户@主机`，Linux 本机可留空）和可打开的应用，
点「启用」并过一次原生确认，当前引擎会自动重启。开着时改目标或应用名单，也会再确认一次。Desktop 启动的本机引擎
只认这里的设置，不继承父进程里的同名环境变量。

无头 CLI / 常驻服务用环境变量：

```dotenv
VORTOCODE_ENABLE_DESKTOP_CONTROL=1
VORTOCODE_DESKTOP_TARGET=ssh:user@desktop-vm.lan   # 或 local（本机 X11）
VORTOCODE_DESKTOP_DISPLAY=:0
VORTOCODE_DESKTOP_APPS=xfce4-terminal,firefox,thunar
```

目标机器需要 X11 会话（Wayland 下 xdotool 控制不了别的窗口），并安装 `xdotool`、`scrot`。经 SSH 时，运行 VortoCode
的机器要能用密钥免交互登录目标机器。推荐给它一台专用的桌面虚拟机（XFCE + X11，自动登录），不要用宿主机自己的桌面会话。

## 实测（2026-10-17，局域网内的测试虚拟机，Ubuntu 24.04 + XFCE，软件渲染）

窗口列表、截屏、启动终端、紧接着输入中文（三次，无丢字）、未安装应用如实报错、疑似密钥拒绝输入、非法按键与名单外应用拒绝。
修过两处：`setsid -f` 对不存在的命令也返回 0（改为先查装没装、后查进程）；紧接着上一步动作就打字会丢开头的非 ASCII 字符
（改为输入前停 0.3 秒、字符间隔 40ms）。
