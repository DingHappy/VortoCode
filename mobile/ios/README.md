# VortoCode iPhone 原生版

同仓库 SwiftUI iOS 17+ 工程，支持 iPhone/iPad。已实现原生概览、目标列表/详情、设置、系统账号认证、钥匙串会话恢复和退出；服务端开启手机写入后，另有「等你决定」「随手记」、撤销和加密推送（见下文「主人终端」）。没有 Android 工程；聊天、语音与手机远程接管仍未接入。

## 使用

1. 先按 [账号接入说明](../../docs/MOBILE_ACCOUNT_ACCESS.md) 配置常驻助手、归属、HTTPS 入口和账号回调，部署含原生接口的 Gateway。旧服务尚不支持 `/api/mobile/native/*`。
2. 在 App 连接设置粘贴 Desktop 给出的手机入口。只保存规范化 origin，不填管理员令牌或账号密码。
3. 点击“登录并打开助手”。系统 ASWebAuthenticationSession 完成原账号登录；回到 App 后直接进入原生页面，不再浏览 Web 概览。
4. 原生页可手动或下拉刷新，查看目标的下一步、阻塞和版本。资料、主聊天与目标分别保留核对状态和时间；部分来源失效时保留该来源上次内容并标为历史。读取失败或进入后台后，已有内容标为历史；授权失败清除视图。
5. 设置中的“退出手机账号”清除本机钥匙串会话并请求服务端撤销本设备授权。服务器未确认时，欢迎页与设置页显示“远端退出待确认”，可手动重试。Desktop 登录、其他手机与服务器任务独立保留。

App 使用 [Apple 系统认证会话](https://developer.apple.com/documentation/authenticationservices/authenticating-a-user-through-a-web-service)，不读取浏览器 Cookie、不注入 JS、不采集密码。返回 App 的只有 60 秒、一次性、再次绑定 PKCE 与 state 的交接票据；账号授权码和访问/刷新令牌均由 Gateway 处理。原生 `vcn_` 会话只访问手机原生读取/退出接口，保存为 WhenUnlockedThisDeviceOnly 的 Keychain 记录，不进入 UserDefaults。

会话最长 8 小时，重开 App 可恢复；Gateway 重启、换归属、撤销或过期后需重新登录。原生入口地址还保存在 App 自己的 UserDefaults，隐私 API 原因清单见 PrivacyInfo.xcprivacy；不代替正式发行前的网站/账号隐私披露。

历史读取只保留在本次进程内存中，不落盘。进程重开后从钥匙串恢复受限会话并重新读取，不能无网恢复目标；退出、授权失效或更换入口会清除读取视图。设置中的“来源核对”列出各来源状态与上次核对时间，可重试读取。安全存储失败与网络读取失败分别提示。

退出重试只使用原助手地址与该旧会话的受限凭据，不影响后来登录的新会话。同一项正在撤销时不能重复提交；没有定时或后台自动重试。待撤销凭据仅暂存于当前进程，不写回钥匙串、不用于恢复登录。关闭 App 后若仍未确认，请从 Desktop 的设备授权中撤销。钥匙串清除失败会保留原登录并提示解锁重试，不显示退出成功。

目标页支持按原记录状态筛选，以及搜索名称、下一步、阻塞说明；空格分隔的关键词可跨字段同时匹配，英文忽略大小写。搜索与筛选只在本次已读取记录中进行，不发起额外服务端搜索，也不保存查询词。界面显示匹配数/已读取数；如服务端只返回最近 100 条，会说明更早记录不在搜索范围内。“清除搜索与筛选”会恢复全部并收起搜索界面。

目标详情支持直接刷新与下拉刷新。单条目标暂时不在最新读取结果里时，保留最后查看到的版本并标明历史；不能据缺席推断已删除或已完成。重新取得该目标后更新原状态、下一步与版本。详情页仍只读，不改写或验收目标。

正式构建和真实手机仅允许 HTTPS。Debug 模拟器才允许 HTTP loopback；不接受 LAN HTTP、不跳过 TLS 校验。ATS 仅启用 local networking 支持隔离回环测试，没有全局 arbitrary-load 例外。

## 主人终端：等你决定、随手记、撤销、推送（ADR-011）

服务端开启手机写入（`VORTOCODE_MOBILE_WRITES=1`，要先完成 WireGuard 直连）后，App 登录时自动在 Secure Enclave 里生成钥匙并绑定，
多出两个标签页；服务端没开时 App 保持只读，不显示可执行按钮。

- **三把钥匙**，私钥都不出这台手机：日常钥匙签随手记、撤销、拒绝和推送登记；批准钥匙创建时要求生物识别，
  所以「批准」必须过 Face ID（录入新的面容 / 指纹会让它失效，需要重新登录绑定）；推送钥匙只用来解密推送正文。
- **等你决定**：列出助理拟好、等你批准的 Codex 动作。卡片上的对象和正文就是签进去的那一条，服务端按摘要核对，
  内容变了就拒绝。读过外部内容后拟的动作会标出来。拒绝不需要 Face ID。
- **随手记**：一句话记成你的一个目标（来源记为你本人），之后可以让助理整理；网络重试不会记两次。
- **我替你做了什么**：助理和手机记下的改动，逐条可撤；撤不回的下游影响（例如提醒已经推过）照实列出。
- **推送**：登录后申请通知权限并登记。推送正文加密，苹果只看到占位文字，由 `VortoCodeNotification` 扩展用推送钥匙解开；
  解不开就保留占位文字。与 IM 双发一段时间后再切换。

签名合同见 [手机客户端边界](../../docs/MOBILE_CLIENT_ARCHITECTURE.md) 的「主人终端写入」。验证到的程度：

| 层面 | 状态 |
| --- | --- |
| 签名报文与服务端逐字节一致 | `RequestSigningTests` 用服务端生成的对照向量钉住 |
| Swift 签名能被服务端验证 | 已用 CryptoKit 签名、服务端 `verify` 验证过 |
| 服务端加密的推送能被 iOS 系统 API 解开 | 已用 `SecKeyCreateDecryptedData(.eciesEncryptionStandardX963SHA256AESGCM)` 在 macOS 上解开 |
| 模拟器 Debug / Release 真机二进制编译 | 通过（未签名） |
| Secure Enclave、Face ID、APNs、通知扩展共享钥匙串 | **需要真机签名安装验收**；模拟器没有 Secure Enclave，Debug 构建退回软件钥匙，仅供联调 |

签名构建要在 Apple 开发者后台给 `com.vortocode.mobile` 开启推送，并登记扩展 `com.vortocode.mobile.notification`
与钥匙串分组 `com.vortocode.mobile.shared`；`aps-environment` 当前为 development，正式分发前改为 production。

## 构建

打开 `VortoCodeMobile.xcodeproj`，scheme `VortoCodeMobile`。修改配置后在仓库根执行 `xcodegen generate --spec mobile/ios/project.yml`。MobileCore 为无远端依赖的 Foundation 合同包，可用 `swift test --package-path mobile/ios/Packages/MobileCore` 独立测试。

`VortoCodeMobileTests` 是会话生命周期测试目标，以依赖替身控制认证、存储和读取/撤销 API；覆盖取消后的迟到回复、过期/跨入口会话、授权失败、钥匙串失败、退出失败重试及并发保护。可在 Xcode Test Navigator 单独运行，无需合成账号服务。它不能替代下面的真实系统登录交互验收。

模拟器 App：`/private/tmp/vortocode-ios-derived/Build/Products/Debug-iphonesimulator/VortoCodeMobile.app`。设备 Release 编译输出在 `/private/tmp/vortocode-ios-device-build/Build/Products/Release-iphoneos/VortoCodeMobile.app`，当前禁用签名，不能直接安装到真实手机。真机仍需 Apple 开发团队和签名；没有 IPA、TestFlight 或 App Store 发布。

AppIcon 复用原仓库品牌资源；正式商店发行前仍需处理原图透明通道与发行材料。

### 本地开发归档

仓库根目录提供独立交付工具，使用本机 Xcode，不上传 Apple、不注册设备、不自动申请描述文件：

```sh
python scripts/build-ios-app.py inspect
python scripts/build-ios-app.py archive --development --output build/ios-delivery/<独占目录>
python scripts/build-ios-app.py verify --output build/ios-delivery/<同一目录> --require-current-source
```

`--development` 只使用尚未过期、包含设备、匹配本 App 且签名证书在本机可用的开发描述文件；必须唯一匹配。默认支持 Xcode 管理的本地描述文件，手动管理的文件可加 `--manual-signing`；两种方式均不传 `-allowProvisioningUpdates`，不申请更新，并在归档后核对实际使用的描述文件。没有 `--development` 则生成无签名归档，不能直接安装真实手机。已有输出目录拒绝覆盖。

输出包括 Release iPhone arm64 的 `.xcarchive`、私有构建日志和 `receipt.json`。回执记录实际 bundle 版本、签名类别、源码 commit、是否有未提交改动及源码摘要；构建期间源码变化时不生成回执。验证会重新检查 App 资源摘要、设备平台、HTTPS 策略、认证回调、开发签名、嵌入描述文件和权限一致性。`--require-current-source` 另要求当前工作区源码与归档时一致。

开发签名归档仅适用于描述文件授权的设备，仍需要设备连接、开发权限及实际安装验收。归档回执始终将真机安装、线上连接与 App Store 发布标为未验证，不能用编译或签名成功替代这些证据。当前图标带透明通道，仍不是 App Store 发布候选；交付工具也不提供上传或发布功能。

## 验收

详见 [ACCEPTANCE.md](ACCEPTANCE.md)。已有真实账号服务源码 + 临时 SQLite + 真实 Gateway + Swift API 的 HTTP 联调；iOS 模拟器也实际执行系统登录、原生目标详情、进程重开恢复和退出。所有账号/目标均为临时合成数据，未使用真实账号或 97 线上服务。

UI 测试 `NativeFlowUITests` 需要 `VORTO_FIXTURE_URL` 指向隔离脚本提供的临时回环凭据端点；没有该参数会明确跳过，不能将跳过算为登录验收。该端点仅存在于验收脚本临时服务，不在产品 Gateway。

隔离脚本 `--preview` 另提供只绑定回环的 `/mode` 控制，模拟来源不可用或 503；故障仅在正常认证成功后改变临时读取响应，不会绕过产品授权。它用于验证历史保留与恢复，不是手机真实飞行模式/网络切换验收。

原生交互验收使用原 PersonalGoals 创建两个临时目标（跟进中/有阻塞）；脚本中的 `/goal-progress` 仅更新其中一个固定合成目标，验证详情页能读到实际新版本。`empty-goals` 模式模拟该目标未出现在读取列表中，用于验证历史保留。控制接口只存在于临时验收脚本，不在产品 Gateway。

`logout-failed` 模式会在临时服务的真实退出处理器前返回 503，保持原授权未撤销；恢复 `normal` 后，App 的手动重试必须到达真实处理器。最终还检查临时设备记录均已撤销，不能只以欢迎页出现判定远端退出成功。
