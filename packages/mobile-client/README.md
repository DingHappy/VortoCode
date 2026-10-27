# Mobile read client

独立的 TypeScript 通信包，无运行时依赖，不导入 Desktop、Tauri、React 或原型代码。浏览器/TypeScript 手机实现可以复用它。现有同仓库 iPhone 0.2 原生只读版以 Swift MobileCore 按同一领域读取合同实现，不要求 Swift 执行 TypeScript；账号认证在系统窗口完成，返回原生概览/目标页面。两种实现独立保留来源核对状态与历史内容，不新增业务数据库。

读取模块提供 `bootstrap()`、`snapshot()`，以及管理读取顺序的 `createMobileReader()`。独立的 `./pairing` 导出配对会话：本机生成凭证、复用原请求发起/检查/取消，并在电脑批准后连接读取。取消仅影响持有凭证证明的本次请求/设备，不授权业务写入。没有管理员登录、聊天写入、业务批准、自动重试或本地凭证持久化。绑定的 `instanceId` 来自用户在同一 HTTPS 服务核对的配对结果，后续响应必须一致。

默认只收 HTTPS origin，不接受用户信息、路径、query 或 fragment。`allowInsecureLoopback` 仅允许明确开启的本机 HTTP 测试，不能开启远端 HTTP。领域读取固定 GET，配对固定三个 POST 路径且仅在 body 中携带本请求证明；两者均 omit cookies、no-store、禁止重定向。自定义原生 fetch 适配器也必须遵守这些选项。没有为跨站网页放开 CORS。

服务端协议版本、实例、会话标识、来源结构和目标 revision 都会在使用前校验。新增字段可忽略；未知写能力不会启用。来源失效保留旧内容时，`status` 为 unavailable，`checked_at` 仍为旧时间。`current` 只说明读取状态，不说明目标已达成、画面已连接或设备能接管电脑。全体来源失效时整个读取状态也为 unavailable。

刷新代次、取消及断开会丢弃迟到结果。认证失败、换凭证或实例不匹配会清除连接视图。每次请求都有超时且不自动重试；读取器没有自行启动轮询或后台任务。

```sh
npm ci --ignore-scripts
npm run check
npm test
```

测试需要 Node >= 22.18，使用内置 TypeScript strip，不需要测试框架。仓库根目录运行 `python scripts/check-mobile-contract.py` 可以验证真实回环 HTTP 接口与此客户端的互通和撤销行为。该脚本只用临时目录，关闭 Gateway lifespan，不启动调度器或模型。

详见 [模块与拆仓决定](../../docs/MOBILE_CLIENT_ARCHITECTURE.md)和 [浏览器只读入口](../../mobile/README.md)。现有 [视觉原型](../../prototypes/mobile-assistant/README.md)继续只用演示数据，不导入本包，不接真实设备令牌。

独立的 `./browser` 导出 `createBrowserClient()`：同源 HttpOnly BFF 的 status/login/bootstrap/snapshot/logout，使用 same-origin Cookie，无 JS 账号凭证。实例来自已认证状态，再用原 readBootstrap/readSnapshot 校验。登录跳转限定原账号服务与已登记回调，断开/退出/状态更新使旧读取失效。原 Bearer 与 pairing 模块仍 omit cookies，未扩大其权限。
