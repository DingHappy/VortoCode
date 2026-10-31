# Project 中的 N 入口只读托管

2026-10-31，B1 + B2a 实施切片。一个 runtime 可以托管多个 Telegram/钉钉绑定，bot 定义、账号
绑定、主通知入口与模型执行额度分别登记。仍使用原 MainAgent/工具运行层，不创建另一份
任务库、调度器或开发执行池。它尚不是完整 General→Project 协作协议。

## 已实现范围

- `bots[]` 是固定只读策略的逻辑定义；没有外部账号的 bot 可以只登记，不启动模型或任务。
  `bindings[]` 引用 bot；同一 bot 可绑定不同账号/渠道，默认各自历史，共享一个聊天执行占位。
- 全部新入口均使用 `research_read`，包括指定的主协调入口：仅项目内读取/检索，不加载
  主助手资料，不联网、不写代码、不递归派活。开发/审批仍由 Desktop 或原总助手处理。
- `primary_bot_id` 决定预留聊天槽，`primary_binding_id` 决定全局文字通知的唯一接收入口。
  不是“最后连接的账号”。主入口离线/关闭不自动改投其他绑定，也不授予研究入口原审批权。
- 共享 `max_model_turns`，默认 2，主 bot 保留 1 个；每 bot 同时至多一个聊天执行，清理也
  占位。该计数只覆盖本托管器的 IM 回合，不覆盖 Desktop、其他 Project 或供应商总费用。
- 托管纯文本先在原 SessionStore 保存账号级事件记录，再通过原 SessionActors 启动回合。
  Web runtime 注入原 actor 实例，不另建执行池。繁忙时回复“未受理”并记录拒收；不缓存或
  自动排队。只读状态与停止不申请模型槽；停止请求与执行真正退出分别表达。
- `runtime_lifespan` 持有一份 workspace lease；每账号继续使用原 polling lease。关闭单个
  绑定只清自己的任务/订阅/路由，真正收尾后才释放账号占用。重复停止不重复打断清理。
  迟到清理核对精确实例/路由代次；释放失败保留该绑定，不能当作已经关闭再启一个。

旧 `VORTOCODE_IM` 单入口路径保留，包括原任务 worker 与审批。新托管器不为只读 bot 注册
开发 worker，也不会让它们覆盖原 worker。没有改变旧任务归属或为跑通联动重建预算根。

## 显式宿主配置

下面 **JSON** 是本切片消费的配置；[完整 RFC](MULTI_BOT_DESIGN.md) 中的 YAML 是后续目标
结构示意，两者不能混用。文件必须是宿主显式指定的绝对路径，位于项目目录之外；不扫描
项目里的 bot 文件。上限 64 KiB，拒绝重复键/未知字段；POSIX 下拒绝其他用户拥有或组/其他
用户可写的文件。仅含身份元数据和环境变量前缀，不放凭据值。

```json
{
  "version": 1,
  "primary_bot_id": "project-guide",
  "primary_binding_id": "owner-main",
  "max_bots": 8,
  "max_bindings": 8,
  "max_model_turns": 2,
  "bots": [
    {"id": "project-guide", "policy": "research_read"},
    {"id": "research-a", "policy": "research_read"},
    {"id": "evidence-reader", "policy": "research_read"}
  ],
  "bindings": [
    {"id": "owner-main", "bot_id": "project-guide", "channel": "telegram",
     "account_id": "123456", "owner_ref": "paired-owner", "env_prefix": "BOT_GUIDE"},
    {"id": "owner-research", "bot_id": "research-a", "channel": "telegram",
     "account_id": "234567", "owner_ref": "paired-owner", "env_prefix": "BOT_RESEARCH"}
  ]
}
```

注册定义/绑定限额分别默认 4，当前可配置 1—32；模型槽也为正整数，多个 bot 时至少 2。
这些是准入限制，不是无限扩容或跨机双活保证。上例的第三个 bot 没有入口，也不会后台运行。

每个 Telegram 前缀读取 `_TOKEN`、可选 `_OWNER_ID`、可选 `_BOT_USERNAME`；未给 owner 时
使用原账号配对记录。例：`BOT_GUIDE_TOKEN`、`BOT_GUIDE_OWNER_ID`。每个账号需分别沿原
配对流程核对；`account_id` 是 token 的账号前缀。钉钉读取 `_CLIENT_ID`、`_CLIENT_SECRET`、
`_OWNER_ID`，account_id 对应 ClientID。这里只检查本地适配器身份与配置一致，不代替供应商
认证或真实渠道连通验证；不会临时改写进程里的通用凭据变量来轮流装配不同账号。

`owner_ref` 是宿主已核对的同一主人映射，各渠道仍使用自己的配对 ID；同渠道的 owner ID
不一致则拒绝整次启动。通用/渠道 `ALLOW_FROM` 继续生效：空白名单仍全拒，不允许为研究
入口扩入其他用户。未知 bot、重复账号、主绑定归属不符或凭据/身份不匹配均拒绝，不猜归属。

在明确选定的项目中启动（凭据预先由自己的宿主环境提供）：

```bash
VORTOCODE_WORKSPACE_SCOPE=project \
VORTOCODE_IM= \
VORTOCODE_IM_ROLE=interactive \
VORTOCODE_IM_BINDINGS_FILE=/absolute/host-config/im-bindings.json \
vc server
```

不能与 `--im`/非空 `VORTOCODE_IM` 同用；General/Scratch、旧 notify 模式和无效配置拒绝
启动，不以普通高权限助手回落。配置在启动时读取，尚无热更新/管理界面或新增 HTTP 路由。
现有 `/api/im/status` 保留主入口字段并增加 `bindings`，列出逻辑 ID、状态与非秘密活性；
registered/ready 不表示供应商已认证或网络已连接，需分别看通道活性。

宿主停止/异常退出后，原已完成会话仍可恢复；逻辑 bot/账号/策略身份改变不导入别人的
历史。关闭入口不自动取消原后台项目任务。一次 poll 失败只降级该绑定，不重做模型任务。

## B2a：原始事件记录与恢复

新托管入口只消费具有渠道原生身份的纯文本：Telegram 使用 `update_id` 与 `chat.id`；
钉钉使用消息体 `msgId` 与 `conversationId`，不使用连接帧 ID 或会轮换的 webhook。
缺身份的输入明确拒绝，不用文本哈希、时间或当前绑定生成替代 ID。附件在本切片拒收。
旧单入口不强制这份新合同，不因此获得去重保证。

事件键由 provider、已核对的账号、事件种类、原始会话与事件 ID 计算，与项目路径、逻辑
bot、绑定 ID 和启动代次无关。因此同账号换绑或移动到另一项目时，已记录的旧事件不会
重新运行。目标项目/会话指纹单独冻结，消息正文只存摘要，不在账号记录里复制用户文本。
同身份的正文摘要冲突会暂停新输入，不能猜哪条是真的。

记录沿用原 SessionStore 格式和原子保存路径，不建立另一个任务库。账号记录位于原
`VORTOCODE_IM_LOCK_DIR`（默认 `~/.vortocode/channel-locks`）下的 `inputs/<账号摘要>`，
内部仍是 `.vortocode/web_sessions/im-inputs.json`。该状态目录必须位于 Project 之外，
由同一宿主长期保留；先取得原账号 polling lease 才打开记录，收尾结束后才释放 lease。
部署时应把此目录作为宿主持久状态备份，不能随临时工作区清理。

当前每账号最多 2048 条记录，读取最多 2 MiB，不自动淘汰或按 TTL 清空。满额只拒绝新
输入，已有去重记录仍有效；归档/迁移工具尚未实现。保存使用文件 fsync、原子替换及
可用时的目录 fsync。检测到内容变化、损坏、符号链接、重复 JSON 键或已初始化目录内
的文件缺失时拒绝继续；保存失败也暂停新受理，不在写失败后启动模型。
整个账号状态目录被删除、状态路径被改动、恢复旧备份或底层文件系统丢失不能由这份
单机记录自行证明。这里不声明跨机一致性、完整断电恢复或供应商的 exactly-once。

处理状态只有 `claimed`、`settled`、`interrupted`、`rejected`：

- `claimed` 是本机已落盘认领；重启后转为 `interrupted`，只提示核对，不自动重跑模型。
- `settled` 只说明本机回合/命令返回，不证明模型成功、渠道送达、任务验收或目标达成。
- 忙时/额度满/附件拒收也留下 `rejected`，旧事件重传不会在空闲时突然执行。需要重试时
  由主人发送一条新的原始消息，不能把拒收当作排队。
- `/new`、`/persona` 和 `/stop` 同样核对并保存事件身份；重传旧 `/new` 不清新上下文，
  重传旧 `/stop` 不打断新回合。状态/帮助无需模型和写入；记录不可用时，渠道停止也拒收，
  主人仍可在 Desktop 的原会话请求本地停止，或关闭宿主绑定。重复停止不打断清理。

首次启动发现 B1 历史却没有托管标记时拒绝自动接管，需要人工核对迁移；当前没有迁移
工具，不应删历史或生成伪事件 ID 来绕过。去重只覆盖这份账号记录开始可靠保存后的输入。
Telegram offset 和钉钉 ACK 仍是原 transport 行为；ACK/游标推进不等于本机受理，故障
时可能需要主人重新发新消息，不能把防重放当作可靠送达承诺。

执行/冷取消清理沿用 SessionActors，模型占位一直保留到原 actor 及其冷取消收尾真正退出。
Native 历史保留托管标记，Web 不会为同 SID 装配更高权限的普通助手。Desktop 可查看历史、
刷新快照及请求本地停止；继续提问需回原 bot 或另开会话，尚未实现实时镜像与跨入口接管。
托管回合不另启后台摘要模型请求。

## 验证与未覆盖边界

离线测试使用真实 runtime 生命周期、托管 poll 循环、账号 lease、共享 Agent 核心及临时
项目；渠道/模型都是替身。覆盖 1/2/4/8 入口、K=2/3 额度、一个 bot 多入口、混合渠道、
精确通知、主入口故障、冷取消、重复取消、关闭失败、启动回滚、迟到清理和历史归属。
另覆盖原生事件提取、重复/冲突、跨重启/跨项目换绑、旧命令重传、忙时拒收保留、存储失败、
容量与损坏、未知认领恢复、actor 启动前写失败、共享 Web actor 冷取消占位及只读历史。
它们不代替真实 Telegram/钉钉、原生 Desktop、Windows 原生或供应商费用验证。

真实启用后，每条获受理的问题会请求宿主已配置模型并可能产生费用；仍有每回合 12 步
限制，但不是 token/金额/墙钟硬预算。没有新增定时模型轮询或完成自动唤醒。

剩余门禁包括持久排队与公平性、历史/容量迁移、General 到项目的授权映射、固定证据/恢复
兼容及正式研究任务往返。去重和 actor 的离线证据只覆盖上述 B2a 范围；渠道真实重传、
供应商身份语义及部署恢复仍待真机验证。没有微信适配、跨项目自动链或 peer 消息自动执行；R1—R10 的
完整实施条件不因本切片解除。首版全只读策略也不代表任意同步文件/正则操作都有响应时限。
