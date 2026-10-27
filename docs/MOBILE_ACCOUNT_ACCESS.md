# 同账号手机入口

用户决定（2026-10-25）：手机与常驻助手沿用同一个 VortoCode 账号，日常使用不要求手动核对码。手机模块继续放在本仓库；账号服务沿用已有独立 `vortocode-account` 仓库。

## 当前实现

手机 `/mobile` 已接入独立客户端 `vortocode-mobile-web` 的授权码 + PKCE 登录。账号验证、授权确认和密码输入都发生在原账号服务。回调由助手服务交换凭证，手机脚本只使用同源 HttpOnly Cookie。

登录身份必须与本运行端登记的 `(issuer, subject)` 完全相同；邮箱、显示名或手机自报身份不能授予访问。登录后显示原助手资料、主聊天登记情况和个人目标摘要，保留来源时间与失效状态。高级手动配对仍独立可用。

当前连接范围是 **当前入口对应的运行端**，状态接口明确返回 `discovery_scope=current_runtime`。账号下所有助手的云端目录、跨主机选择和网络连通尚未接入，不能把账号设备清单当作助手目录。

## 会话和权限

- 浏览器会话最长 8 小时，刷新可恢复。账号访问/刷新令牌仅留在服务进程内存，不写入网页存储或运行端授权文件；服务重启后需重新登录。
- Cookie 设置 HttpOnly、SameSite=Lax，HTTPS 下设置 Secure；POST 要求匹配的 Origin，登录回调绑定一次性 state、浏览器证明及会话代次。
- 读取沿用原设备授权存储中的 5 分钟租约。BFF 每次读取都核验账号；退出立即停用此 BFF 会话并撤销其读取租约。退出或换归属后，迟到的登录/续期结果不能重新建立连接。
- 不授予管理员、模型密钥、聊天写入、任务批准或远程接管权限。原账号服务为手机客户端禁用模型密钥接口，授权码和刷新令牌绑定各自客户端。

## 启用条件

1. 已构建 `mobile/`，并配置 Gateway 管理员身份。
2. 可信 Desktop/服务端通过原内部接口将助手登记到经过核验的账号。Desktop 设置 → 账号与用量 → 手机与常驻助手已提供“关联当前账号 / 解除关联”；原生确认后使用钥匙串中的身份完成操作，不要求用户复制令牌。
3. Gateway 配置 `VORTOCODE_MOBILE_PUBLIC_ORIGIN=https://assistant.example.com` 与原 `VORTOCODE_ACCOUNT_URL`。
4. 账号服务配置 `ACCOUNT_MOBILE_REDIRECT_URIS=https://assistant.example.com/api/mobile/account/callback`；多个已登记回调用逗号分隔。不配置时手机客户端保持禁用。
5. 两侧部署更新后再启用 HTTPS 入口。反向代理保留实际 Host，并仅从可信代理接受协议转发；请求 origin 必须与配置一致。

回调地址必须完整精确匹配，不接受通配地址、任意跳转或远端 HTTP。仅隔离本机测试允许 loopback HTTP。不会自动开放公网端口。

会话存在于单个进程，现阶段需单进程服务或将同一浏览器路由到同一进程；没有实现多进程共享会话或原生 Keychain。云端目录、推送、原生 App 和真机验收仍是后续工作。Desktop 关联交互已有源码与测试，实际安装客户端及原生确认窗口尚未点验。

## 源码与验证

主链路：`account_oauth_http.py` → `mobile_browser.py` → `/api/mobile/browser/*`；前端复用 `packages/mobile-client/src/browser.ts` 和原领域投影。内部管理员桥和租约细节见 [ACCOUNT_RUNTIME_BRIDGE.md](ACCOUNT_RUNTIME_BRIDGE.md)。

`python scripts/check-mobile-browser.py` 使用现有账号服务源码、临时 SQLite、合成账号及两个回环 HTTP 服务，实际经过登录表单、授权确认、PKCE 回调、BFF 读取和退出，并验证跨账号拒绝、拒绝授权、Origin 和管理员隔离。没有使用真实账号、生产数据库、模型或调度器。

浏览器及隔离测试结果见 [mobile/ACCEPTANCE.md](../mobile/ACCEPTANCE.md)。这些合成测试本身不代表部署、Desktop 安装或生产迁移，实际部署须另存版本与迁移回执。

## Desktop 关联入口（2026-10-26）

先登记常驻助手，再在设置的“手机与常驻助手”卡片刷新状态，点击“关联当前账号”。原生确认窗口明确显示目的地址及将发送短期账号证明；客户端只从已登记的 `@assistant` 条目解析目的地，不能选择任意 URL 或项目实例。关联不覆盖其他账号，换账号须先明确解除原关联。

只允许 HTTPS 或已登记的本机回环隧道。普通局域网 HTTP 不传送账号证明；不会为了启用手机入口自动改网络、开公网端口或重启远端。显示入口 URL 表示服务已配置该地址，不证明账号服务回调、手机网络或 HTTPS 已验收。

原生端发送与页面读取一致的 instance_id + revision，服务在原存储锁内校验后写入；身份或版本变化会拒绝旧操作。解绑撤销账号读取租约；退出 Mac 本机账号保留常驻助手归属与独立手机会话。手动配对单独管理。

手机 BFF bootstrap 现在显式只返回实例、设备展示信息和只读能力，不再展开内部租约回执中的资源凭证；账号与资源凭证都不通过该响应交给网页脚本。

接下来的顺序：更新并配置实际常驻服务/HTTPS 与账号回调 → 安装客户端并点验原生关联/解除 → 真手机完成登录、刷新、撤销 → 云端助手目录与跨主机选择。聊天、审批和远程接管继续使用各自明确授权。

## iPhone 原生会话（0.2.0）

原生 App 使用 ASWebAuthenticationSession 打开 `/api/mobile/native/authorize?challenge=...&state=...`。Gateway 继续使用已登记的 mobile-web 账号回调，账号凭证仍留服务进程。账号回调加载一个无脚本、无缓存的返回文档，随后打开固定 `vortocode-mobile-auth://callback`，不接受任意 return URL。

回到 App 的票据只在 60 秒内有效、一次性并绑定 App 内生成的 verifier/state。POST native/exchange 后得到 `vcn_` 原生会话；GET native/bootstrap、native/snapshot 与 POST native/logout 只接受该会话的 Bearer，不借 Cookie 或管理员身份。读取沿用原目标、资料与主会话投影，写能力为空。

原生会话最多 8 小时，账号令牌刷新由 Gateway 串行处理；App 将受限会话保存在本设备 Keychain。退出、换归属、撤销、到期和服务重启都会失效。页面仅显示既有目标状态，不新增任务库或推断今日完成。当前只连接已知入口对应实例，云端助手目录仍待实现。

原生工程与验收见 [iPhone 说明](../mobile/ios/README.md)。

## 独立手机网关与首次关联

`src.web.mobile_server:create_app` 可以独立运行，只加载手机读取、账号桥和静态资源路由，不加载 Agent、模型、调度器或消息桥。`deploy/vortocode-mobile.user.service` 使用常驻助手原工作目录读取领域记录，仅允许写入 `.vortocode/mobile-devices` 安全授权目录。要求独立管理员令牌、精确公开 origin、绝对状态根路径及发布标识；仅监听 `127.0.0.1:18081`，单进程运行。

`deploy/Caddyfile.mobile-proxy` 只代理手机浏览器/原生登录与读取接口、明确静态资源；管理员关联、手动配对管理和执行接口不公开。运行时日志过滤请求 URI 和请求/响应头，避免错误日志记录 OAuth 回调和 Cookie。`/healthz` 仅为传输检查；是否关联账号须检查 `/api/mobile/browser/status`。

旧 Desktop 尚无账号关联界面时，管理员可在自己的电脑运行：

```sh
python scripts/link-resident-account.py --ssh-host vorto-code
```

打开其打印的回环地址，在原账号服务确认要关联的账号。工具使用一次性 PKCE 授权，通过已有 SSH 管理连接访问远端私有账号桥，服务端重新核验稳定身份并以 instance_id/revision 登记；已有归属会拒绝覆盖。不会读取 Desktop 钥匙串或保存账号令牌，关联完成或失败后退出本次临时授权设备。页面只监听本机，10 分钟未完成会失效；回调后跳转到不含授权码的结果页。该流程属于首次部署，手机日常仍使用同账号登录。

账号服务升级涉及 `devices.client_id` 与 `auth_codes.client_id` 两个兼容加列，旧记录默认 `vortocode-desktop`。降级到不识别 client_id 的旧账号服务前，必须先停发新令牌、吊销所有 mobile-web 设备及其令牌，并作废未使用的 mobile-web 授权码，避免旧逻辑将手机凭证当作 Desktop 凭证。保留原 Desktop 设备与令牌；无需删除新增列。生产升级、账号归属、浏览器验收及手机实机验收应分别记录。
