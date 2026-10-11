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


## Desktop 手动入口与资源/提示

插件页的MCP卡查询当前会话状态，连接读取已启用的无凭据HTTP配置及目录；不需要开启环境opt-in，
按钮本身就是一次显式操作。重复在途连接拒绝，任务运行期间拒绝更新；结束前重核会话/agent/执行身份。
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
