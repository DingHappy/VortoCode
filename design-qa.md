# 助手侧栏、电脑工作区与动态详情 QA

日期：2026-10-24。范围：现有生产 React 组件的浏览器渲染与交互；不是完整原生客户端验收。

## 对照目标与证据

用户要求学习交互形式，沿用 VortoCode 现有身份、设计 token、任务记录和确认机制。
Muse 的品牌头像、Codex 的品牌插画、通话和多设备能力不属于此次复制范围。

源图目录：`/Users/happyding/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_j119lch7sv7s22_b700/temp/InputTemp/`。

| 来源 | 文件 | 原像素 | 对照内容 |
| --- | --- | --- | --- |
| Muse | `0edd0da9-1ea8-47b2-884a-e45c2e43ea11.png` | 5120×2656 | 居中详情，左侧步骤、右侧结果 |
| Codex | `136c03ff-4131-475e-9236-7d44d222b351.png` | 3456×2030 | 身份下的电脑入口 |
| Codex | `08965cef-7d9a-4f36-b969-8e0605a0689a.png` | 3456×2030 | 聊天旁的电脑画面 |

实现：`http://127.0.0.1:5198/qa/assistant-activity.html`，Ego Space 203。
此页使用实际组件和产品样式，聊天、归档、任务与批准计数为合成数据；电脑 iframe 为专用虚拟机的真实只读画面。
没有调用模型，没有点击批准，没有发送远程键鼠输入。独立窗口按钮在夹具中不执行原生命令。

证据目录：`.vortocode/acceptance/20261024-assistant-workspace/`。
桌面实现截图为 1728×916 CSS/图像像素、DPR 1；窄屏 390×844、DPR 1。
源图 DPR 无元数据，不推断其真实 CSS 尺寸：仅为诊断对照将 Muse 等比缩至 2048×1062，Codex 缩至 1728×1015。
不同产品的动态内容、授权状态、浏览器边框和视口不同，因此不作逐像素一致性结论。
窄屏夹具单独取消产品最小宽度，用于组件压力检查，不代表原生客户端支持 390px 窗口。

已打开并检查下列合并对照图，源图与实现位于同一张图片：

- `comparison-muse-full.png`、`comparison-muse-detail.png`：全局层次和可读的详情局部。
- `comparison-assistant-full.png`、`comparison-assistant-detail.png`：全局布局及身份/电脑卡片局部。
- `comparison-computer-full.png`：真实桌面宽屏证据，工具栏精简前。
- `comparison-computer-detail.png`：精简后的窄屏画面与源桌面区域，视口差异明确，不据此比较像素密度。

最终详情和侧栏：`10-final-detail.png`、`11-final-feed.png`；深色：`08-dark-detail.png`；
窄屏详情与已收到真实画面的电脑：`07-narrow-detail.png`、`09-narrow-computer.png`；
旧服务降级：`12-detail-unavailable.png`。`05-computer-workspace.png` 已显示真实宽屏桌面。
`06-before-first-frame-rejected.png` 在连接状态先于首帧出现时截图，画面为空，明确排除为画面成功证据。

## 发现、修复与复验

1. [P1，已修复] 详情原来挤在右侧栏，无法容纳步骤和结果。`02-before-detail.png` 对比源 Muse；改为独立 modal，桌面 960×620，左侧 260px 步骤。复验 `10-final-detail.png`。
2. [P1，已修复] 原先 modal 在滚动侧栏祖先下，自动化命中/可见性受祖先裁切影响。改为 portal 到 body；步骤点击、Tab 焦点留在 modal、Escape 关闭均通过，`04-turn-step.png`、`07-narrow-detail.png`。
3. [P2，已修复] 嵌入电脑保留整套重复标题和工具栏，挤占画面。iframe 模式折叠次要工具，保留观察/接管、连接状态与控制权提示；展开/收起已实测，`09-narrow-computer.png` 显示精简后的真实画面。独立查看模式保留完整工具。
4. [P1，已修复] 虚拟 X 显示器 600 秒息屏，导致空闲后黑屏。当前专用显示已 `xset s off` 并复位，`display-awake.txt` 显示 timeout 0；启动计划增加 Xvfb `-s 0`，实际远端 helper 摘要与本地一致。没有重启用户服务。
5. [P2，已修复] 只拿到摘要时不能显示虚构的步骤。接口失败时只有结果入口、摘要、重试和原对话入口，`12-detail-unavailable.png`；没有把“已回复”改成“已验收”。

## 五项视觉检查

- 字体：沿用现有系统中文字体回退，不推断或复制参考品牌字体。标题 16px/600，正文 13px，步骤 12px；长标题换行，参数采用可换行等宽文本。局部对照确认层级清楚。
- 间距：居中弹层、固定标题、左右独立滚动区、右上关闭入口与参考形式一致；尺寸增大以承载本项目的 Markdown 和确认卡。窄屏改上下布局，详情没有横向溢出。
- 颜色：沿用 n1–n13 和现有语义 token，浅/深主题已查看；选中、失败、等待和未知状态仍可区分。参考绿色“完成”没有套用到本项目的“已回复”。
- 图像/图标：保留既有 VortoCode 身份头像与 Lucide 图标，未以代码重画参考产品品牌资产。电脑内容来自真实 VM；不同桌面主题和应用是预期差异。
- 文案：明确“过程记录 / 结果与后续 / 打开原对话 / 仅观察 / 接管操作”；摘要缺失和最终回复缺失分别提示；执行结束不表示验收完成。

## 交互与可访问性

- 电脑卡片位于身份之后，打开后桌面宽度保留左侧聊天；关闭回到原动态面板。
- 步骤点击显示该步保存的参数/结果；请求仅标记“已记录”。
- native dialog 有可访问名称，关闭按钮有标签；键盘 Tab 不落到背景，Escape 关闭，不触发背景快捷键。
- 检查窄屏、深色、来源不可用、详情不可用、步骤选中、工具展开与只读连接。
- Runtime/Log 开启后的本轮观察没有捕获到 JS exception/error；不是历史日志的全面扫描。
- 全程合成批准次数为 0；观察 iframe 关闭后远端 observer_viewers=0。原用户的接管窗口保持不动。

## 独立验收边界

原生控制通道报 native pipe startup failed，本轮未用其他 UI 技术绕过。
因此 Tauri 内 iframe 实际加载、原生独立窗口切换、App 重开后的完整流程仍待原生验收。
真实 SSH 测试验证了自有隧道、状态读取、连接复用和按 surface/id 释放；不能代替上述 GUI 验收。
`.97` 旧 HTTP 服务未部署新的归档详情端点；旧服务走已验收的摘要降级，新本地运行包包含该端点。

0.2.18 已签名、公证并安装；buildId `20261024T154809397Z-f74cdf37`，安装后 sourceFingerprint 重算一致。
首次检查原 App/runtime PID 仍为安装前进程，已请用户退出重开；安装文件版本不能作为窗口已运行新版的证据。
随后用户实际打开新版：App PID 61759，runtime PID 61763/54514，健康正常；不存在的验收会话请求命中新详情路由，确认新运行包已加载。原生 GUI 交互验收仍独立待验。

完整工程门禁：Python 5067 通过/10 跳过；E2E 25 通过；前端 974 通过；Rust 116 通过/2 忽略。
另显式运行真实 SSH 忽略测试，1 通过。全仓 cargo fmt --check 存在历史格式差异，未批量格式化无关文件；本轮 native 文件 rustfmt --check 通过。

## 实施检查清单

- [x] 复用原任务、对话归档与权限机制；没有新增任务数据库。
- [x] 电脑卡片、同页工作区、动态步骤/结果弹层已接入 App。
- [x] 参数脱敏、选定 rid、详情缺失降级和旧连接释放检查通过。
- [x] 组件浏览器渲染、键盘、主题和窄屏检查完成。
- [ ] 原生 App 重开后的完整 GUI 验收，单独报告。

残余 P3：未来可对工具输出增加折叠/复制和产物跳转；当前可通过原对话查看完整内容。
在本次组件模式和浏览器交互范围内，没有未处理的 P0/P1/P2 发现。

final result: passed

## 后续行为回归：详情刷新（2026-10-24）

本节是上述布局的读取行为修复，不重新宣称完整原生 App 视觉验收。
详情标题增加刷新按钮，未完成归档仅在详情打开期间核对；完成后不继续自动网络请求。
失败保留记录和当前选中步骤，标题明确显示“上次记录 · 待核对”；稀疏事件不再清除参数，保留工具错误。
Ego Space204：合成归档，真实组件；未连接远程电脑或真实模型。

证据 `.vortocode/acceptance/20261024-activity-refresh/`：
`01-recorded-error.png`、`02-refresh-failed.png`、`04-updated-without-closing.png` 和 `05-narrow-header.png`。
浏览器观察到首次“暂无最终回复”，随后自动更新；`03-first-poll-rendered.png`拍摄时已更新，不作前态截图。
上述图片已查看：1728×916和390×844，DPR1；窄屏document宽度390，刷新/关闭均在视口内。
步骤选中在刷新失败/重试/自动更新后保留；Escape关闭；本轮Runtime/Log无JS异常，批准计数0。
Space204结束并关闭，没有恢复用户持有的旧空间。

完整门禁Python5068/10跳过、E2E25、前端986、Rust116/2忽略通过。本次源码未打包安装；上一节0.2.18安装证明属于先前源码快照。
未发现新的P0/P1/P2组件布局问题，原生GUI验收边界仍保留。

final result: passed

## 后续行为回归：精确定位原消息（2026-10-24）

新增普通对话详情到rid锚点的导航与“回到最新”。参考目标是当前聊天组件的原消息上下文，
本节不新增对Muse/Codex品牌或完整原生客户端的保真结论。
Ego Space205使用真实ChatHistory、详情弹层、定位hook和产品CSS，80条合成消息。

证据 `.vortocode/acceptance/20261024-turn-location/`，均已查看：
初版 `01-exact-turn.png` 的sticky提示与上方旧内容形成缝隙；移至滚动区外的独立行后，
`03-final-located.png`展示明确分隔的提示和原消息。`04-final-missing.png`为未定位/重试状态；
`05-narrow-located.png`、`06-dark-located.png`覆盖390×844/DPR1的浅深主题；桌面1728×916/DPR1。

实际核对：精确到turn-12/user；同轮请求与回复标记2项；新增回复前后scrollTop均为2419；
回到最新后标记0、距底部0；不同client环境虽含相同rid，旧定位仍清除；
缺失记录10秒后只提示，补回不会自动抢焦点，手动重试后定位missing/user。
窄屏documentWidth=390，提示条底220.5、目标消息顶240.5，不重叠。Runtime/Log本轮无JS异常。
Space205已结束并关闭，不恢复旧空间。生产前端构建、tsc和991项测试通过。

本次源码未签名安装；完整原生App验收仍独立待验，不把组件和构建证明当原生证明。

final result: passed

## 后续行为回归：手机只读记录（2026-10-24）

手机查看器复用原消息组件、定位提示和主题tokens；新增独立native dialog，不重新声称完整原生App保真。
Ego Space206使用合成消息，80条历史；最终使用和App相同的React.StrictMode。
`.vortocode/acceptance/20261024-phone-location/`下截图均已查看：
`01-detail-to-phone.png`为桌面详情到原消息；`02-missing-phone-message.png`为缺失记录提示；
`03-narrow-phone.png`、`04-dark-phone.png`为最终浅深色窄屏；`05-empty-phone.png`为空快照。
桌面1728×916/DPR1；窄屏390×844/DPR1，dialog370×812，documentWidth390，无横向溢出。

发现并修复：只含关闭按钮的手机弹窗Tab会移出页面；共享焦点处理现在在可见控件间循环，
从程序定位的tabindex=-1消息也能进入下一控件。单按钮四次正反Tab均保有页面焦点且位于弹窗，
动态详情五个控件循环七次均在弹窗内。Escape关闭返回原入口。
详情到手机原消息时仅有一个dialog打开，焦点turn-12/user，同轮两条标记，输入框/表单0。
记录追加前后scrollTop均2401；回到最新后标记0；缺失状态提供重试，空快照不声称从未聊过。

tsc、生产构建及1000项前端测试通过。Runtime/Log本轮无JS异常，Space206已结束关闭。
未访问真实手机账号、模型或桌面；本次源码没有打包安装，真实手机进程和完整原生App联调仍待验。

final result: passed

## 后续行为回归：归档状态准确性（2026-10-24，0.2.19）

归档缺失状态现为待核对；保存的running显示“记录为执行中”，无实时动画。
原对话同步使用保守的历史状态还原；未闭合/未知项保留展开和关注提示，实际active步骤仍有动画。
文件写入回顾只计明确成功的工具记录，命令/测试等以记录条数说明，不宣称已验收的修改或测试结果。
步骤详情显示实际事件时间和有限非负耗时，未知值不补造；较早开始时间不套用于缺少时间的结束事件。

Ego Space207，真实组件与合成归档；证据 `.vortocode/acceptance/20261024-record-status/`：
`01-unknown-record.png`、`02-timeout-metadata.png`、`03-history-states.png`、
`04-narrow-metadata.png`、`05-dark-recorded-state.png`均已查看。
桌面1728×916/DPR1，窄屏390×844/DPR1；宽度无溢出，元数据可换行。
未知/历史running的步骤spinner均0；超时记录UTC为2026-10-24T09:00:04.000Z，耗时1.3秒；
Hook受阻保留原事件与工具。原对话展示1项成功写入记录和3项需关注，没有把未完成写入计为修改。
窄屏部分裁切的文本节点导致一次自动化点击失败，检查后滚动步骤区并点击完整按钮通过。
本轮Runtime/Log无JS异常；Space207已结束关闭，无真实模型、工具、账号或桌面输入。

完整工程门禁Python5069/10跳过、E2E25、前端1005、Rust116/2忽略通过。
组件视觉/交互范围内没有未处理P0/P1/P2；原生完整GUI和真实手机/远端服务联调独立待验。
0.2.19签名、公证、安装状态以DESKTOP_ACCEPTANCE第72节交付证据为准。

final result: passed

### 0.2.19本地交付状态

前面第69–72节对应改动已合并到0.2.19并签名、公证、安装。buildId为`20261024T171220889Z-d0dd7f5b`，冻结源码摘要重算匹配。
首轮签名时间戳失败保留；仅对重试进程隔离代理环境后成功，原系统配置和签名/公证校验保持。
安装后独立runtime smoke553ms、签名/Gatekeeper/票据通过；不调用真实模型。
首次安装检查仍是旧App/runtime进程，已请用户退出重开。原生窗口控制接口timeoutReached，完整GUI验收仍待验。
签名、安装和重开证据分别记录于DESKTOP_ACCEPTANCE第72节及其交付补记，不覆盖尚未做的手机/97真实联调。
