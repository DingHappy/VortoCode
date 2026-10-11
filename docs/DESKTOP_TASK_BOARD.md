# Desktop 工作看板合同

首版入口：工作台“看板”（复用原 inbox 页），默认看板视图；“展开看板”扩大浏览区域，
“按项目查看”保留原收件箱。仅 Desktop 增加界面；CLI/Gateway 的任务与 Goal 生命周期
保持原合同。实现模块为 `components/TaskBoard.tsx` 与 `lib/taskBoard.ts`，App 只接导航。

## 数据与身份

复用原 `GatewayClient.getRuntimeInbox()` / `GET /api/runtime-inbox` 的 version=1 摘要，
没有新增 REST 写接口。运行时来自原 Desktop 注册/连接，凭据仍由原装配维护；看板不
获取或保存凭据。摘要不是完整 TaskLedger 扫描，也不能累计成为覆盖凭据。

卡片 key 为 JSON 编码 `[runtimeId, kind, id]`；会话/Goal 筛选为 `[runtimeId, id]`。
任务 owner_session 的单个 sid- 前缀仅在 Desktop 转换为会话 ID，原记录保持不变。
项目、会话、Goal 同时选择时取交集；项目改变才清空两个子筛选。会话无法从摘要证明
属于某个 Goal 时，不猜测关联。来源或筛选对象移除时保留失效选择，不扩展到其他项目。
未知摘要版本不生成卡片。刷新复用原请求代次/迟到响应隔离，未增加轮询器或持久索引。

## 状态与操作

- 会话 idle/inactive 不入列；completed 明示会话结束，不是任务验收或 Goal 达成。
- Task 新增可选 lifecycle 摘要，分开记录 execution/review/handling/validity。研究 done
  已验收且当前交接已处理、依赖可核验才进入已完成；开发 done 交接已处理显示“不代表
  验收”。没有新摘要、损坏或未知时保守待核对。不能把浏览或回执作为验收。
- 匹配原恢复合同的旧停止任务保留在可展开历史中，不改原失败结果，也不证明原交接
  已处理。未匹配的失败继续待处理。投影不改变 Goal 状态或写入任何任务。
- Goal achieved 仅消费原权威状态；通过数直接使用原 progress，不按完成任务数推断。
- 失联保留旧摘要，来源/卡片均标未知且禁用看板操作，时间仍为旧 generated_at。
- 点击任务只导航原项目、原 owner_session、task_id；真正的回答/恢复/验收/回执继续
  使用原任务卡入口，保持权限、轮次与结果版本校验。Goal 入口导航原项目 goals 面板。
  “所有工作区”下入口指向当前 runtime，按钮显示项目；General/无来源/失联禁用。

数值为当前摘要内事项数，既不是全量，也不去重成唯一任务数。每来源 100 会话/100
事项/200 Goal/200 Task 的既有上限写在折叠说明中，空列仅表示当前摘要/筛选无事项。

## 验证与下一步

`lib/taskBoard.test.ts` 检查 done 边界、取消/依赖、跨项目同名身份、筛选交集、来源
缺失/失联、Goal 权威状态、未知版本和不改写源数据。`components/TaskBoard.test.tsx`
检查失联禁用、历史内容保留、General/无来源和未知版本。可复用隔离客户端入口
`desktop/qa/task-board.html` 使用原 RuntimeInboxPanel，导航回调记录目标；它不接真实
Gateway、provider 或持久化，不能据此宣称跨 Gateway/原生包全链路通过。

在 `desktop` 中执行 `npx vite build --config qa/vite.config.ts` 生成独立 `qa/dist`，不
覆盖正式 `dist`；可用 `--outDir /tmp/vorto-board-isolated` 冻结到临时目录。fixture 纳入
TypeScript 检查，正式 Vite 入口仍为 index.html，不打包 QA 页面。

权威生命周期摘要已实现。下一步增加同项目跨会话 Goal 关联、精确 Goal 定位和台账分页。拖拽执行、自动推进与跨项目 Goal 需要独立合同。


## lifecycle version=1

原 `/api/runtime-inbox` version=1 兼容增加任务 `lifecycle`；没有新写 API。

| 字段 | 合同 |
| --- | --- |
| execution / kind | 原执行状态 / 已知任务类型或 unknown |
| review | not_submitted / pending / accepted / rework_requested / not_applicable / unknown |
| handling | pending / handled / not_applicable / unknown；不是验收或持久交付 |
| validity | valid / invalid / not_applicable / unknown；只解释摘要内的依赖观察 |
| round / revision | 可读协作轮次或 null；可用交接的内容 SHA-256 或空字符串 |
| column / label | 服务端工作投影与说明，客户端仍核对合法状态组合 |
| recovered_by | 原恢复合同匹配的接续 task ID，旧状态保留在历史 |

`src/workflow/tasks/task_summary.py` 只处理原 TaskLedger 的当前 200 条摘要前缀，来源查询总额
2048 次，复用原有界图核对，不追加文件读写。缺来源/损坏/预算不足 unknown；观察到
当前轮次、归属或结果版本冲突 invalid；合法已固定失效不会自动解除。valid 不证明
没有外部临时变更、全图覆盖或执行已停止。共享锁仍只有单进程保证。

客户端缺字段/不支持版本/矛盾结果回退旧 done 待核对；不能用短文案伪造完成。原按项目
视图共用生命周期标签/注意事项与来源身份，原任务卡的操作合同不变。

`qa/task-board-server.py --assets /tmp/vorto-board-lifecycle-assets` 提供可复用独立临时
源码副本、TaskLedger 和原 Inbox/交接路由，禁止模型构造。QA HTML 的 `?live=1` 使用
原 GatewayClient，只在 QA 配置把原生 HTTP 插件替换为浏览器 fetch；生产配置不采用
该替换。它仍不是已安装 Desktop/完整常驻端进程的验收。服务的测试突变端点只作用于
专用临时台账，不进入正式 Gateway。


### 精确 Goal 定位

看板/原列表的 Goal 与 Goal 事项携带精确 ID，任务卡可查看已有 goal_id。
原目标面板通过单项 GET 读取（不受摘要200项上限限制），显示 ID 与读取状态。
404/失败/身份不匹配不展示旧目标的执行操作；提供原 ID 重试和明确返回列表。
项目重置/客户端变化/新定位丢弃迟到响应。导航不建立关联或改变验收/交接。
同项目跨会话新关联仍待独立合同，参见 PRD 第38节。
