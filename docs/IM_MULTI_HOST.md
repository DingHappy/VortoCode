# Project 中的 N 入口只读托管

2026-10-31，B1 实施切片。一个 runtime 可以托管多个 Telegram/钉钉绑定，bot 定义、账号
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
- 尚未迁移持久输入受理/SessionActors：繁忙时明确回复“未受理”，不缓存为已排队消息。
  只读状态与停止不申请模型槽；停止请求与执行真正退出分别表达。
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

## 验证与未覆盖边界

离线测试使用真实 runtime 生命周期、托管 poll 循环、账号 lease、共享 Agent 核心及临时
项目；渠道/模型都是替身。覆盖 1/2/4/8 入口、K=2/3 额度、一个 bot 多入口、混合渠道、
精确通知、主入口故障、冷取消、重复取消、关闭失败、启动回滚、迟到清理和历史归属。
它们不代替真实 Telegram/钉钉、原生 Desktop、Windows 原生或供应商费用验证。

真实启用后，每条获受理的问题会请求宿主已配置模型并可能产生费用；仍有每回合 12 步
限制，但不是 token/金额/墙钟硬预算。没有新增定时模型轮询或完成自动唤醒。

剩余门禁包括持久输入受理与渠道事件去重、原 actor 统一控制、排队公平性、General 到项目
的授权映射、固定证据/恢复兼容及正式研究任务往返。重启后渠道重传仍需后续去重证明，不能
声称不重复产生模型请求。没有微信适配、跨项目自动链或 peer 消息自动执行；R1—R10 的
完整实施条件不因本切片解除。首版全只读策略也不代表任意同步文件/正则操作都有响应时限。
