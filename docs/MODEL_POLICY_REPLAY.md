# 固定公开题的模型对照与离线路由回测

这套工具补充 [模型画像与成本比较](./MODEL_ROUTING.md)：历史任务存在难度差异，
因此先用原 LongMemEval-S 入口，在同题、同检索输入、同评分模型下比较两个答题模型。
本轮仅交付工程实现和离线 token 预估，没有发送真实模型请求或发布能力得分。

## 当前范围

- 原 `select_holdout` 固定分区：六类记忆题各两题，共 12 题，每题重复两次。
  两次重复相关，不能当作 24 个独立题目。该分区不是新隔离、从未看过的测试集。
- 两组只改变答题模型；冻结代码、数据、检索版本、请求参数、依赖环境、评分模型与端点。
  `cheap` / `strong` 只是操作者指定的标签，不证明价格或能力关系。
- 标准 runner 现在分别接受 `--worker-model`、`--judge-model`、`--simulator-model`。
  各自留空使用原默认服务；评分者和 ToolSandbox 用户模拟器不继承答题模型的参数。
  显式指定无效渠道会失败，不借用其它渠道凭据或静默回落。
- 当前离线回测仅接受 memory-only v3 完整报告。ToolSandbox 仍可经原入口运行，
  但其工具交互轨迹尚未接入本轮配对回放。

LongMemEval 仍使用官方提示词配合替代评分模型，不是官方评分模型条件。
评分输出只接受去除首尾空白后的 `yes` / `no`；解释文字、空响应和含糊输出均失败，
不再通过文本包含 `yes` 就判为通过。评分错误不能当作答题模型答错或通过。

## 先离线准备与估算

准备原标准评测使用的公开 LongMemEval-S 数据文件及官方 `evaluate_qa.py`。
运行环境需要已有的 `evals` 与 `llm` 可选依赖；本轮未新增依赖或变更锁定版本。
下面的 `BENCH_ROOT` 由运行者设置为自己的缓存目录，模型名全部是需替换的虚构标识。
预先缓存 `tiktoken` 的 `cl100k_base` 编码后，规划过程可完全离线运行。

```sh
python -m evals.standard.model_plan \
  --longmem-data "$BENCH_ROOT/longmemeval_s_cleaned.json" \
  --judge-prompt-source "$BENCH_ROOT/evaluate_qa.py" \
  --memory-variant compact \
  --cheap-target default:worker-a --strong-target default:worker-b \
  --plan-out "$BENCH_ROOT/routing-plan.json" \
  > "$BENCH_ROOT/reservation-estimate.json"
```

规划器在临时 SessionStore / SQLite FTS 中实际检索公开题目，不读取生产会话、任务或记忆。
答题输入不含标准答案，评分提示可包含标准答案。估算包括两组答题与基准评分请求，
输入采用 cl100k 代理 token 的 1.5 倍余量、每次 2048 输出额度；评分输入另留
4096 代理 token 给尚未生成的答题文本。这些是规划假设，不能证明其它分词器的上界。

本次 compact 离线估算为每组 **563,042**、两组合计 **1,126,084** 代理预留 token，
共计划 **96 次请求**，实际模型请求 **0 次**。这不是实耗、报价或供应商硬限额。
实际生成长度、过滤和服务端计量可能不同；无显式价格时费用保持未知。

规划文件绑定数据、源码、题目和两组请求目标，默认所有题型都走 `strong`。
应在真实对照前按已有训练证据编辑 `routes_by_type`，每类只能指定 `cheap` 或 `strong`。
不要按本轮答案或评分逐题挑赢家。文件生成时间未被可信服务认证，回测始终输出
`plan_timing_verified: false`，不会把这次小样本比较作为自动上线的依据。

## 真实对照入口

下面命令带 `--live`，**会向所配置的服务发送公开题目并可能产生费用**。
本轮开发没有执行它。先替换目标、固定独立评分模型，并根据本机估算设置每组额度。
单组例子如下，第二组仅把 worker 改为规划中的另一个目标，使用相同预算与其它参数。

```sh
python -m evals.standard --live --suite memory --split heldout --repeat 2 \
  --memory-variant compact \
  --worker-model default:worker-a --judge-model default:benchmark-grader \
  --longmem-data "$BENCH_ROOT/longmemeval_s_cleaned.json" \
  --judge-prompt-source "$BENCH_ROOT/evaluate_qa.py" \
  --tokens 650000 --out "$BENCH_ROOT/arm-a"
```

本地预留限制不是服务端硬预算；超时或取消不证明远端停止计费。过滤、失败、缺失用量
保留原失败证据，不自动重试或换渠道。`650000` 是此处规划示例，不是费用承诺。
需要费用比较时，沿用 `VORTOCODE_MODEL_PRICES` 显式配置真实渠道的输入/输出单价；
每次响应记录单价快照。缺价格不当作免费，不换汇，明确零单价才显示零费用。

## 离线核对

```sh
python -m evals.standard.model_replay \
  "$BENCH_ROOT/arm-a/report.json" "$BENCH_ROOT/arm-b/report.json" \
  --plan "$BENCH_ROOT/routing-plan.json" > "$BENCH_ROOT/replay.json"
```

`report.json` 需替换成 runner 实际输出的时间戳文件名。回测拒绝旧版、未完成、被过滤、
请求归属不全、用量缺失或无法严格解析的报告；也拒绝源码、数据、评分端点、返回模型标签、
价格快照或其它配对条件变化。两组的实际答题输入哈希必须逐题相同。
建议使用 compact 的稳定来源标识；baseline 的随机本地会话 ID 可能使输入不一致，
此时应重新生成可比报告，不要事后删除或规范化哈希来绕过核对。

输出包含两组与路由策略的正确题次数、分题型统计、相对 strong 的逐题退步，
以及答题、**基准评分**、合计三类计量。`observed_study_total` 是两组全部已记账请求，
`counterfactual_costs` 和费用差额只是选定策略的假设投入，不是实验已省下的钱。
请求目标及返回模型标签不能证明供应商内部权重；同标签会单独标记。
价格估算不包含缓存优惠、阶梯价或未返回费用，也不是供应商账单。

当前策略只按题型进行首次静态选模，决策模型调用数为零。它没有模拟“便宜模型失败后，
评判是否继续并让强模型接手”的上下文、额外调用和返工成本；基准评分开销也不是生产
评判器开销。后者需要真实顺序执行及独立结果核对，不能拼接两份独立答案来宣称验证。
这些命令不更新模型画像、默认路由、任务台账或运行配置，不启动任务或部署服务。
单元测试的模拟响应仅验证工程合同，不作为模型能力题库或节省比例证据。
