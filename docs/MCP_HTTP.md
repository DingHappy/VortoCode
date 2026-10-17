# MCP HTTP 配置与能力边界

第50节新增、第51节扩展显式选择的 `streamable_http` 工具子集。按
[2026-07-28 官方HTTP传输合同](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
实现单端点JSON-RPC POST、JSON/SSE结果、每请求元数据及工具参数头镜像。
这不是完整MCP符合性声明；工具列表/调用已实现，旧版声明的resources/prompts提供显式读取工具。

## 显式配置

使用原 `config/mcp.yaml`，以下为关闭状态的示例，代码不会创建或启用该服务：

```yaml
servers:
  - name: demo
    transport: http
    protocol: streamable_http
    protocol_version: '2026-07-28'
    url: http://127.0.0.1:8765/mcp
    timeout: 30
    credentialed: false
    enabled: false
permissions:
  rules:
    - id: demo-count
      action: allow
      tool_names: [count]
      server_names: [demo]
      priority: 100
```

仅在用户已有授权的 external/unattended 会话中，显式启用配置并选择原MCP入口。
Web仍要求 `VORTOCODE_WEB_MCP=1`；没有自动启用、扫描第三方配置或扩大凭据权限。
Desktop项目/Scratch的local边界仍拒绝外部MCP；General不加载仓库Skill。
Desktop插件页现有当前会话连接状态、手动连接/重试；local会话显示受限且禁用按钮。`ask`规则没有人工确认门时仍拒绝。

未写 `protocol` 的旧配置继续使用 `legacy_rest`，走旧 `/health`、`/tools`、`/tools/call`。
新模式只访问配置中的原始完整端点，不追加路径。2026版本不发送initialize/GET/DELETE；显式2025版本进行initialize、initialized通知、Session-Id传递及退出DELETE清理。
`protocol`拼写错误或不在已支持版本列表内显式拒绝，不自动降级、重发或跟随HTTP重定向。
显式支持2025-03-26、2025-06-18、2025-11-25；初始化只能协商到这三者之一。会话404标记失效，下一次由用户手动重连；原工具调用绝不自动重放。

## 有界读取与失败

- 每次JSON/SSE响应累计最多1MiB；SSE最多256个有数据事件。
- 一次目录发现最多32页、256个原始工具，游标最多4096字符；重复/损坏游标和工具报错。
- 目录发现含分页整体默认30秒，配置范围 `(0,120]` 秒；调用沿用相同HTTP超时。
- 超界或读取失败上抛，原注册表保留旧目录；明确成功空目录可移除该服务工具。
- 按第49节使用服务/工具完整身份；工具源不变，不改变权限规则优先级。
- JSON-RPC响应必须匹配本请求id，服务错误、isError和损坏结果保持失败。
- SSE允许请求内进度/日志通知；采样请求、MRTR输入请求及未知服务消息拒绝，不自动补充输入。
- 工具schema中的 `x-mcp-header` 验证路径、类型及唯一性，非法注解排除对应工具并记录告警。
  嵌套properties路径支持string/integer/boolean；值用标准Base64 sentinel安全编码，单值最多8192字符。
- 取消退出原HTTP上下文并关闭本地响应，不重试；不证明远端副作用、费用或执行已经停止。

没有后台订阅、自动目录重载、自动模型检查、自动推进或provider硬预算。
旧标准HTTP会话及请求SSE已实现；尚不支持2024双端点SSE、断流恢复、sampling/elicitation/roots、MRTR或自动订阅。2026资源/prompts发现和完整符合性仍待验证。
同连接并发刷新序号、工具版本执行绑定和通用名称编码仍待独立合同。
实际包、测试及未验收层见 [Desktop验收记录](./DESKTOP_ACCEPTANCE.md) 第50节。

## 工具返回数据适配

既有 stdio、legacy REST 与 Streamable HTTP 的 `tools/call` 使用同一结果信封校验：
`content` 必须为带类型的内容块列表，`isError` 若存在必须为布尔值，`structuredContent`
若存在必须为对象。结构化数据经原注册表、执行器和 Agent 包装层保留；复杂输出使用 JSON
序列化，保留布尔值、null、中文及资源链接，不再用 Python 容器字符串代替 JSON。
无结构化字段的列表结果保持 JSON 数组，原内部纯文本结果保持文本。
服务端 `isError: true` 及损坏结果仍为失败，不根据文本内容猜成功，也不自动重放调用。

此处遵循 [MCP 工具结果的数据合同](https://modelcontextprotocol.io/specification/2025-06-18/server/tools#tool-result)，
只校验信封，不宣称验证完整 `outputSchema` 或全部内容块内部结构。未知类型块作为数据保留，
资源链接不自动访问，服务端元数据不作为本地授权。图片附件适配及音频限制见下一节。
旧版资源/提示通用读取工具沿用原结果格式，不把它们误当成 `tools/call` 结果。
原来源污点、Build/权限门、连接器凭据与 HTTP 读取上限继续适用。
回环测试覆盖 HTTP 到原 Agent 包装层的成功/失败数据链路，不代表第三方服务或真实模型验收。

### 内联图片与二进制内容

`tools/call` 的 `image` 内容块可进入原工具图片队列，再由原回合循环作为 `image_url`
送入下一次模型消息。仅接受内联 Base64、PNG/JPEG/GIF/WebP 的 MIME 与基础文件签名，
不读取路径、不跟随 URL、不执行图片内容。签名检查不是完整图片解码、像素上限或视觉能力证明。
每个结果最多提供四张、单张解码后最多 512 KiB、合计最多 1 MiB；原队列仍对整批工具合计
限制四张，超出会另加未注入说明。HTTP 原响应大小限制继续适用，可能先于媒体限制拒绝请求。

文本投影移除已识别 `image` / `audio` 的 `data`，以及嵌入资源的 `resource.blob`，并提供
每块 `mcp_media` 状态。`offered` 只代表交给附件通道，不能证明图片已进入本批队列、供应商
已接收或模型已经看懂。坏编码、类型不匹配和超限只跳过对应图片，保留其它文字及结构化结果；
音频与嵌入 blob 暂不加载，明确标记 unsupported。服务端报错不注入图片，不自动重试。
资源/提示通用读取结果、未知块和应用自定义 `structuredContent` 不按猜测的二进制格式重写。
这里移除已知媒体字段，不能保证应用自定义字符串中没有 Base64。

原传输结果不变；Agent 文本、Hook 和工具事件使用移除已知二进制字段后的投影，附件沿原消息
历史保存路径保留，不能将此视为“不保存图片”。权限、来源污点和模型选择保持原合同。
JSON/SSE 回环服务与两种 Agent 协议的模拟模型验证不代替第三方服务、真实视觉模型或 GUI 验收。


## Desktop 手动入口与资源/提示

插件页的MCP卡查询当前会话状态，连接读取已启用的无凭据HTTP配置及目录；不需要开启环境opt-in，
按钮本身就是一次显式操作。General 也可连接已配置的命名凭据连接器，使用原私有凭据与只读筛选合同；Project/Scratch 不加载这类凭据。接入范围与操作步骤见 [扩展接入](./EXTENSIONS.md)。重复在途连接拒绝，任务运行期间拒绝更新；结束前重核会话/agent/执行身份。
GET /api/extensions/mcp?session=<sid>、POST /api/extensions/mcp/connect?session=<sid>沿用原Gateway鉴权。
General只放行这两个精确端点，不放行项目extensions/inspect或任意mcp子路径；项目local仍拒绝接入。
失败保留原工具/连接，重试同步替换原工具目录并清理旧manager，旧连接清理未确认单独显示。
观察状态可以是connected/partial/error；目录连接不代表调用已验收，历史观察时间不能证明服务持续在线。
客户端按client/session/connection代次忽略迟到响应，不后台轮询或自动重试。运行包重启后会话连接需重新建立。

旧版server声明resources/prompts时，目录增加mcp_resources_list、mcp_resource_read、mcp_prompts_list、
mcp_prompt_get四个通用工具，以原完整服务身份包装；仍是外部不可信内容及Build/权限门。
资源/提示读取不自动将提示插入系统、不执行脚本，也不订阅。列表页返回nextCursor，仅代表一页。
旧版初始化未声明tools时，不发送tools/list；只装配已声明资源/提示的通用工具，无声明则返回成功空目录。断开状态仍拒绝发现。2026发现合同不据此更改，不能称完整MCP。


第52节：observed_at固定成功目录观察时间；失败尝试以attempted_at另记，不把旧目录伪装为刚重新读取。
发布新连接后的取消不恢复旧观察；失败候选清理有界且最终释放连接中标记，未确认清理单独说明。

第53节：初始化/包装失败、尚未向会话移交manager时，也执行独立5秒清理额度。
清理错误或超时保留原连接异常，以固定无敏感信息的异常note标记清理未确认，
会话只投影该固定标记，不展示原错误内容。清理期间取消继续传播CancelledError，并记录未确认。
已经移交会话的连接仍由原生命周期清理；所有连接尝试最终释放在途门，不自动重放调用。
该超时约束合作式异步清理，不是杀进程或远端任务停止/费用停止证明。
