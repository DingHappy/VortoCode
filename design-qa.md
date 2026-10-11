# Desktop 日常布局视觉核验（2026-10-10）

范围：源码 0.1.12 的日常页面样式与原组件隔离夹具。完整 App 的导航、恢复和原生窗口
不在夹具中；因此不能用本报告替代整体验收。原生暂缓是用户明确要求，继续独立开发。

source visual truth path:
`/Users/happyding/.codex/generated_images/01a1007e-7878-7cc2-ba53-4223298b24a2/exec-e5bb6e77-65bd-4b9e-830f-0217f1353d76.png`

implementation screenshot path: `/tmp/vorto-daily-final.png`
viewport: 1586 x 992 CSS px；source 和最终截图均 1586 x 992 pixels；deviceScaleFactor=1。
state: 结果已输出、过程收起、步骤侧栏打开、浅色。另有 empty/working/dark/narrow 截图。
full-view comparison evidence: 已分别打开参考图和最终浏览器截图，比较主区域分配、内容
顺序及控件位置。参考是设计意图；240px 导航、820px 正文上限、15px 正文来自已整理
的产品基线，未逐像素复制生成图较大的字体/导航。合成报告不作为真实模型结论。
focused region comparison evidence: 检查输入框、侧栏与标题，字号用浏览器计算样式核对；
模型文字最终13px。1024 x 768 下页面 scrollWidth=1024，侧栏为覆盖层；上下文弹层
left665/right945/top575.5/bottom695，在视口内；工作区域菜单 top348.5/bottom560。

**Findings / comparison history**
- 初次隔离截图：任务步骤按钮纵排。夹具缺少原 App 的 workbench-toggle class，已补，
  不是产品按钮回归。修复证据 `/tmp/vorto-daily-result-fixed.png`。
- Markdown 标题原规则有固定深色文字，深色主题可读性不足；新增主题变量覆盖。
  修复证据 `/tmp/vorto-daily-dark.png`。
- 模型/权限文字原高优先级规则压为11px，已以同等优先级改13px和更深中性色；
  最终计算样式13px，证据 `/tmp/vorto-daily-final.png`。
- 完整 App 视觉/导航未捕获。夹具复用原 TurnTimeline、SessionInfo、ProjectPicker、
  ContextStatus、MarkdownMessage 和产品 CSS，但不能证明原生窗口及全部 App 布局。

**Required fidelity surfaces**
- 字体：系统中英文字体，正文15px/1.75，标题22px，控件13px；无字体或装饰资产新增。
- 间距：稳定双栏/按需340px侧栏，24px最小正文边距，输入框无重阴影；窄窗口覆盖层。
- 颜色：复用原浅/深色 tokens；少量蓝色选择与发送入口；未覆盖用户已保存主题。
- 资产：文字品牌、原 Lucide 图标，无聊天头像或虚构图像资产，未用CSS绘制标识。
- 文案：过程标签来自活动、步骤来自计划；示例页明示合成数据，未知不推断成功。

**Interactions**
已操作空白/执行/结果切换、侧栏开关、上下文开关、工作区域选择/退出、输入与合成发送。
合成发送 active=true/open=true/pickerDisabled=true。Runtime/Log 开启后的观察轮次
`page.events()` 未发现 exceptionThrown/entryAdded；不是所有历史日志的扫描。

**Implementation checklist**
组件/布局浏览器检查已完成；完整 App 同视口对照与真实导航需后续独立验收。

**Follow-up polish**
使用真实会话内容核对长文本、表格和审批卡的整体节奏；原生/真实 provider 不推定通过。

final result: blocked
阻塞仅指完整 App 设计一致性证据，原生验收已按用户要求暂缓；不会据此阻塞独立构建、
签名运行包检查或保留本轮已通过的组件证据。
