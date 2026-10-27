# iPhone 入口

- 本目录是独立 SwiftUI iOS 工程，继续与 Gateway 同仓库，不复用 Desktop 的本机管理员令牌。
- 原生版使用 ASWebAuthenticationSession 完成账号认证，回调只接收绑定本次 PKCE 的短期交接票据；账号授权码由 Gateway 交换，不读取 Cookie、不注入 JS、不收集密码。受限原生会话保存在此设备 Keychain。
- UserDefaults 只保存规范化入口地址，原生受限会话仅存设备 Keychain。正式构建只允许 HTTPS；HTTP 回环仅限 Debug 模拟器，不放宽远端 HTTP 或证书校验。
- 浏览器打开不等于账号已登录、助手在线或任务已完成；未接入的聊天/审批/推送不显示可执行按钮。
- 修改 `project.yml` 后用 XcodeGen 生成工程；生成的 xcodeproj 应提交，derived data、签名资料和个人 Xcode 设置不提交。
- 验收区分 Swift 合同测试、模拟器构建、模拟器交互、真机签名安装和线上服务，不互相替代。
