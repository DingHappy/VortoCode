# 共用动态时间线

Desktop 助理面板的「动态」与 iPhone「动态」从同一个只读投影读取近期记录：通知、可撤销的主人数据改动、任务单当前状态、给 Codex 的动作及最新观察、自动审核结论、暂停自动推进的目标。没有新增任务库，也不以 Codex 单轮结束作为整个任务验收。

- Desktop：`GET /api/assistant/timeline?limit=60`，沿用管理员鉴权与工作区范围限制。
- iPhone：`GET /api/mobile/native/timeline`，要求有效原生会话和已信任的设备钥匙。独立手机网关经回环专用 relay 读取常驻助理同一投影。
- 恢复自动推进：Desktop 用 `POST /api/assistant/goal-advance/resume`；手机用 `POST /api/mobile/native/goal-advance/resume`，正文为 `{"goal_id":"personal-..."}`。手机写入须用日常钥匙签名；常驻助理重新验证转发信封与 nonce。恢复不授予派活批准权。
- 撤销、下发和 Codex 决定复用原有接口和权限。撤销后仍无法收回的影响继续展示；手机批准派活和 Codex 动作仍用 Face ID 批准钥匙。

响应是 `{items, unavailable, generated_at}`；条目包含稳定的 `id`、UTC `at`、`kind`、`title`、`detail`、`status`、`tainted` 与 `actions`。暂停项置顶，其余按更新时间倒序，返回总量受 `limit`（1–200）限制。任务单和动作是当前状态快照，并非完整的状态迁移事件日志。过期 Codex 观察标为待核对。缺文件表示尚无记录，损坏或读不到的来源进入 `unavailable`，其余来源照常显示。

两端在动态打开时定期重读（30 秒），Desktop 隐藏窗口降频；iPhone 退到后台停止。手动刷新可立即重读。失败保留上次数据并提示可能过期。Desktop 普通历史默认折叠，待决定、失败与暂停保持可见。

目标暂停从旧 `dispatches/watch.json` 的 `goal_advance_paused` 迁到 `dispatches/goal_advance_paused.json`。读取兼容旧记录，首次写入在跨进程锁内迁移；恢复和新增暂停只修改各自的目标，巡检不再把旧快照整表写回。空的新文件也优先，避免升级前的暂停再次出现。恢复后按已有巡检节奏和每目标判断间隔继续推进。

验证区分离线服务端与签名/转发测试、Desktop 浏览器模拟交互、Swift 合同测试、模拟器构建和线上/真机验收。合并部署后仍需在 iPhone 安装新构建，并验收新条目、下拉刷新、撤销影响和恢复暂停；本地夹具不证明真实推送或设备接收。
