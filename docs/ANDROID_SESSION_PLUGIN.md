# Android 会话插件：显式绑定、原生观察与确认点按

2026-11-01，首个 Android 实施切片，依赖 [可插拔能力设计](PLUGGABLE_CAPABILITIES.md)。
现在可连接主人已启动的本机 Appium/UiAutomator2 会话，读取绑定 App 的原生界面，并经逐次
确认点按最新画面里的可点元素。原项目会话/工具运行层/确认门/取消与 runtime 清理复用，不建新任务循环。

本轮只做离线协议与生产装配验证，没有安装 SDK/Appium、启动模拟器、连接手机或验收真实 App。
不支持自动发现、创建/删除 Appium 会话、启动模拟器/应用、安装/卸载、权限授予、输入、滑动、
截图、WebView、短信、剪贴板、shell、凭证导出或 iOS。跨 App 认证与系统弹窗交还真人。
这一步的 attach-only 范围避免在会话创建结果不明时丢失远端会话归属；自动创建设备会话另做合同。

## 宿主配置与启用

仅 Desktop 托管、有人确认、`local` 档位的 Project 会话可装配，默认关闭。
IM、CLI、无人值守、General/Scratch、external 档位及 research_read 角色不装配。
宿主手动设置以下变量后，新会话/引擎重启生效；模型和项目文件不能选择驱动或目标：

```text
VORTOONE_ENABLE_ANDROID_CONTROL=1
VORTOONE_ANDROID_SESSION_FILE=/absolute/host-owned/android-session.json
```

配置必须在工作区外，文件 600、直接父目录 700、本人所有、无符号链接；精确绑定一个规范
工作区路径，4 KiB 上限，未知/重复字段和不兼容版本拒绝。文件由宿主创建，插件不生成它，
读取不存在的配置也不创建目录。示例中的身份是合成值，真实值由主人从已准备好的 Appium 会话核对：

```json
{
  "version": 1,
  "workspace": "/absolute/project",
  "endpoint": "http://127.0.0.1:4723",
  "session_id": "host-prepared-session-id",
  "udid": "emulator-5554",
  "package": "com.example.demo",
  "generation": "owner-selected-generation-1",
  "label": "测试模拟器"
}
```

`endpoint` 仅接受带端口的 `http://127.0.0.1` 或 `http://[::1]`，可选 `/wd/hub`；不允许 DNS
主机、登录信息、查询、片段或其它路径，不使用系统代理、不跟随重定向。Appium 在本机承担
真实设备连接；不能借这个地址把任意网络目标或任意 Appium 命令交给模型。
每次调用与确认后重读配置；更换会话/设备/应用须更换 `generation`，旧实例先断开再恢复。
这是受信任本机 Appium 的自报能力核验，不是设备硬件认证或防御恶意 Appium 服务的沙箱。

插件不创建 Appium 会话，外部宿主负责其版本、设备准备和初始副作用。UiAutomator2 通常涉及
设备端服务；本插件不安装或检查其安装条件，也不能把已有 Appium 安装过程的权限副作用算成零。
原应用登录与 Appium 生命周期留在设备/宿主，不做 Cookie 复制或浏览器 profile 迁移。

## 工具与人工接管

| 工具 | 行为 |
| --- | --- |
| `android_status` | 仅看本机绑定及状态；不发现设备，不联网。不输出 session ID、endpoint 或原始 UI |
| `android_resume` | 经原 `INTERACT` 门请主人核对设备、登录、前台应用与代次，再核对 Appium 能力；不创建会话 |
| `android_snapshot` | 先检查设备/应用/原生上下文，再读有界 XML，复核后输出编号、标签、可点标记和新 frame ID |
| `android_tap(frame, ref)` | 只点最新画面的可点非输入元素，每次确认；重新读画面/身份与配置，变化则拒绝；不能传坐标 |
| `android_detach` | 经确认停止本会话插件并释放本地租约；保留外部 Appium 会话、应用登录与待核对记录 |

恢复与点按是写工具，沿用原 build/确认要求；完全信任也不免 `INTERACT`。
刚连接、拒绝恢复、认证字段、其它前台 App、WebView、可见外部交互层、配置变化或执行异常
都会暂停。暂停后不会继续读取 UI、截图或自动轮询；主人处理后明确恢复。插件不自行调模型。
页面内容继续是不可信外部内容，原工具运行层负责污点、权限、审计和回合预算。

XML 原文与编辑框/其子节点值不交给模型，已知 password/OTP/PIN/secret 字段在投影之前拒绝。
256 KiB XML、2000 节点、150 投影项上限，DTD/实体拒绝；HTTP 响应最多 512 KiB，单请求
10 秒，完整观察 20 秒，恢复核验 15 秒。不接受用户传入 XPath、execute、shell 或其它 capabilities。
自定义控件、扫码、普通正文中的秘密仍可能无法识别，不能把过滤当任意 App 脱敏保证。

点按确认绑定本会话的随机 frame 与当前 XML 摘要，只使用当时冻结的元素边界。点按后无论成功
或失败，frame 都不可重用。观察与点按不是设备端原子操作，最后核对后 UI 仍可能变化；也没有
独立核实真实网站/应用账号 ID，`user_confirmed` 只代表主人核对。

## 独占、取消与结果不明

本机资源状态放在 `~/.vortocode/android-control/<sha256(android:udid)>/`，不落项目，不存 UI、
输入内容或 Cookie。复用 `ProcessLease`，不同 Appium 端口/会话/应用共享同一设备锁；同进程
最多 8 个活跃连接，不建等待队列。它约束 VortoOne 协作实例，不阻止外部 Inspector、adb、
其它 Appium 客户端或真人同时操作设备；实际验证需使用专用测试设备并核对外部控制者。

发送点按 POST **之前**，在原设备目录写入并 fsync 一个 `pending.json`：只有绑定摘要、派发 ID、
应用包和代次。成功协议回执后才删除；超时、取消、传输失败或崩溃留下占位，重连/重启后仍阻止
下一次自动点按。它是单个资源的待核对记录，不是另一份业务任务台账，也不证明网站提交恰好一次。

遇到待核对记录，`android_resume` 必须明确请主人等待原 Appium 动作结束、核对原应用/代次
是否已点按，再确认清除占位；插件不重放动作，也不据此将业务任务标为完成。损坏/不兼容记录
直接拒绝，不自动修复或删除，需宿主人工核对后维护。卸载或断开插件不清除这份未知结果。

取消向原任务传播，并保留待核对占位；HTTP 等待结束不证明 Appium 远端动作已经停止。
runtime 退出先让原任务清理，插件标记停止、等待当前工具退出后才释放租约，不删除外部会话。
本机进程崩溃会由 OS 释放租约，持久 pending 负责阻止盲目重试；未声称覆盖磁盘损坏等所有故障。

## 验收与下一步

离线验证包含实际生产会话工厂的端/角色矩阵、真实回环假 Appium HTTP 服务、W3C 触摸命令、
确认期间设备/配置/前台/画面变化、认证/外部 UI 保护、跨端口设备独占、取消、退出等待与持久
结果不明恢复。假服务不是 Android Emulator 或真机验收。

下一步先在主人明确准备的模拟器、单个测试 App 上验证真实协议、拒绝/取消/断连与业务结果；
验收后再补普通输入与滑动。设备管理卡、自动发现/会话创建、第三方安装与 iOS 继续独立推进。

官方依据（2026-11-01 核对）：[UiAutomator2 驱动与 udid/capabilities](https://github.com/appium/appium-uiautomator2-driver)、
[Appium 协议入口](https://appium.io/docs/en/latest/reference/api/)、
[Appium getSession/source/actions 路由](https://github.com/appium/appium-base-driver/blob/master/lib/protocol/routes.js)。
