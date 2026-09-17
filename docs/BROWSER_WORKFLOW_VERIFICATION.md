# 浏览器用户流程验收

`browser.steps` 在已有本机页面探测上增加顺序操作和明确断言。没有配置步骤时仍返回 `verification_kind: page_health`；配置有效步骤时返回 `workflow`。截图文件存在本身不代表业务通过。

以下 `.vortocode/verify.yaml` 示例验证新记录保存后能通过刷新重新读取。元素名称需与被验收应用的界面一致，服务命令也需按该项目配置。

```yaml
profiles:
  save-record:
    serve: npm run dev -- --host 127.0.0.1
    ready_timeout: 30
    browser:
      url: http://127.0.0.1:5173/
      steps:
        - action: fill
          target: {label: 标题}
          value: 验收记录
        - action: click
          target: {role: button, name: 保存}
        - action: assert_visible
          target: {text: 验收记录}
        - action: reload
        - action: assert_visible
          target: {text: 验收记录}
```

支持 `click`、`fill`、`press`、`reload`，以及 `assert_visible`、`assert_text`、`assert_value`、`assert_count`、`assert_url`。后四种断言的期望值使用 `value`，数量断言必须是非负整数。定位可以使用 `role`（可带 `name`）、`label`、`text`、`test_id` 或 `placeholder`，一次只用一种，名称精确匹配。

步骤上限 40，至少有一个明确断言。无效字段、无法识别的操作、无断言的流程都会在启动浏览器前被拒绝，不会退化为只检查页面能否打开。页面加载和操作共用时间预算，失败后的截图与清理另有界限。

每一步记录是否通过、耗时和失败原因；首次失败后停止后续操作，并保留截图及 Playwright trace。trace 未成功写入时不能把流程报为通过。运行时入口继续将文件保存在主工作区 `.vortocode/artifacts/browser-verify/`，避免临时 worktree 删除后证据消失，并记录 run、profile、源码 commit、dirty 状态和本机环境。验收期间源码状态变化会报失败。

仍保留 HTTP/WebSocket 的 loopback 边界及 service worker 禁用策略。该扩展不自动创建 Goal criterion；已有 Goal 验收的 commit 校验仍由 RunManager/GoalLedger 负责。

定位和轨迹接口依据 Playwright 官方的 [Locators](https://playwright.dev/python/docs/locators) 与 [Trace viewer](https://playwright.dev/python/docs/trace-viewer) 文档。

## VortoCode 自身的验收流程

`tests/live/test_pipeline_browser_workflow.py` 使用真实审批 HTML、生产路由及持久状态，在临时仓库构造确定性产出，覆盖：

1. 打开产出、批准、刷新、重新打开，批准状态仍成立。
2. 输入驳回意见、驳回、刷新、重新打开，意见及重做状态仍保留。
3. 故意失败的断言准确标出失败步骤，保留截图和 trace，后面的批准不会执行。

```bash
VORTOCODE_LIVE_BROWSER=1 python -m pytest \
  tests/live/test_pipeline_browser_workflow.py -q
```

这些流程调用本机测试服务，不调用 LLM，也不进行真实 IM 投递。普通测试默认跳过真实 Chromium，需要先安装项目 `browser` 可选依赖及 Chromium。
