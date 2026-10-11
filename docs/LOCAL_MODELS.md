# 本机模型接入

> 2026-10-08后续更新（第45节）：按用户指令8100已关闭，当前选用11500。
> Desktop已保存该实例自己的Key及Qwen3.6-35B-A3B-4bit。新安装包已包含SDK，
> 冻结Gateway的真实只读搜索/工具结果回传回路通过；下文8100发现记录保留为历史。
> 不需要为新Desktop额外安装系统Python SDK；CLI的llm可选依赖合同保持原样。


主 Agent 与 `.vortocode/agents/*.md` 角色继续使用原 OpenAI 兼容客户端、路由、工具与
权限门。可以全用本机默认模型，也可以保留云端默认服务、让指定角色使用本机供应商。
不增加第二个 Agent 执行池。看板仍仅属于 Desktop。

## 当前用户的 oMLX（2026-10-08只读复核）

发现两个已运行的omlx-server，不启动、停止或修改服务：

| 地址 | 只读模型清单结果 | 接入要求 |
| --- | --- | --- |
| `http://127.0.0.1:8100/v1` | HTTP200，`Qwen3.6-35B-A3B-4bit` | 模型清单无需Key，推理鉴权尚未验证 |
| `http://127.0.0.1:11500/v1` | HTTP401 | 必须填该oMLX实例自己的Key，空值不能绕过鉴权 |

8100已通过原Gateway模型客户端list_models复核，**没有发送推理请求**，不证明实际工具
或流式质量。上轮11434/11435/1234的检查不足以判断用户本机模型不可用；保留其历史
未验收记录，本次新增实际服务发现证据。不能把8100清单成功套用到11500或其它服务。

Desktop主模型设置/自定义供应商增加独立“oMLX”预设；官方默认预填8000，用户这里
应改为自己选择实例的实际端口8100或11500。不硬编码当前模型ID或自动替换用户配置。
原角色路由示例（仅适用于已核验模型清单的8100，完整推理仍待验收）：

```dotenv
VORTOCODE_PROVIDER_OMLX_BASE=http://127.0.0.1:8100/v1
VORTOCODE_PROVIDER_OMLX_KEY=
VORTOCODE_MODEL_ROLES=review=omlx:Qwen3.6-35B-A3B-4bit
```

角色文件可用 `model: omlx:Qwen3.6-35B-A3B-4bit`，沿用read工具面及原协议设置。
这份示例未写入用户.env/profile或实际角色目录。11500若为目标实例，需要其自己的
Key与实际模型清单，不能借用云端Key或从聊天中要求粘贴密钥。

## 配置路径

**主 Agent 全用本机**：Desktop 设置 → 模型服务 → oMLX（或通用本机模型）；填服务 `/v1` 地址和实际
模型 ID，保存时沿用原确认/重启流程。空 Key 仅用于本机服务无需鉴权的情况。
模型名以服务 `/v1/models` 实际清单为准，不能把示例当成已下载模型。

**云端与本机角色混用**：先保留已有默认模型服务，再在自定义供应商选择“本机模型”，
ID 使用 `local`，地址填本机服务的 `/v1`。“本机模型”泛用预设仍预填 Ollama 地址；oMLX使用独立预设并按实际端口调整，LM Studio 可改为
`http://127.0.0.1:1234/v1`。供应商保存后沿用原当前引擎重启流程，聊天可选
`local:<完整模型ID>`。不改已有后台项目的配置/运行进程。

CLI/常驻端不需要看板；同一模型路由可用以下环境配置（先换成实际模型 ID）：

```dotenv
VORTOCODE_PROVIDER_LOCAL_BASE=http://127.0.0.1:11434/v1
VORTOCODE_PROVIDER_LOCAL_KEY=
VORTOCODE_MODEL_ROLES=review=local:model-id:tag
```

角色文件例子，`model-id:tag` 必须替换为本机实际模型 ID：

```yaml
---
name: local-research
model: local:model-id:tag
tools: read
max_steps: 8
---
只读研究仓库，报告证据与不确定性；不修改文件或扩大权限。
```

保留模型名中的冒号，例如 `local:fixture:7b` 使用供应商 local，实际发送模型名为
`fixture:7b`。本轮没有写入用户真实 `.env`、Desktop profile 或注册上述示例角色。

## 密钥、网络及执行合同

只有精确解析为 localhost/127.0.0.1 的 HTTP/HTTPS 端点、无 URL 凭据/查询/片段，
允许空供应商 Key。后端使用固定非秘密 `not-required` SDK 占位符，不借云端默认 Key。
本机如启用 token 鉴权必须填自己的 Key，401 如实失败，不自动改用云端模型。
本机 SDK 与 aiohttp 请求不读取系统代理环境；公网端点沿用原代理行为。
远程缺 Key 的供应商仍不可用，现有未登记/无效路由回落规则未改。

使用工具型 Agent 时应安装项目已有 `llm` 可选依赖（含 OpenAI SDK），再验证所选模型
工具调用能力。未安装 SDK 的 aiohttp 普通 chat 可降级；请求带原生 tools 时明确报依赖缺失，不静默丢弃工具。
支持工具 schema 的服务不等于任意模型都可靠支持工具；需单独验收实际模型的
工具调用、流式、上下文和完成质量。当前不自动启动、下载或选择本机模型。

本机不天然满足硬预算 provider 合同，也不新增自动检查/自动推进授权。
任务 done、验收、交接、持久交付和 Goal achieved 继续分开。

## 协议来源与本轮验收

[oMLX官方说明](https://github.com/jundot/omlx)提供OpenAI兼容接口，默认`localhost:8000/v1`。
这只是软件默认值，不能代替本机监听端口发现；本轮两实例实测见验收第41节。

[Ollama 官方 OpenAI 兼容说明](https://docs.ollama.com/api/openai-compatibility) 给出本机
`/v1` 用法及 SDK 占位 Key；[LM Studio 官方兼容接口](https://lmstudio.ai/docs/developer/openai-compat)
说明可通过兼容端点接入。这里只使用原 chat/completions 与 models，不声明完整 API 兼容。

第40节已在独立临时依赖目录用真实 OpenAI SDK 3.14.1 验证工具请求、SSE正文/推理/
参数分片、usage省略与流式中断，并跑通原角色文件→原Agent→只读工具→结果回传。
`VORTOCODE_NATIVE_TOOLS` 在自定义角色装配中沿用原全局设置，0仍明确关闭原生协议。
这些合成服务合同测试不证明实际模型工具质量，用户真实 runtime 的依赖未安装或改动。
测试、本机构建、隔离 HTTP 和未验收层见 DESKTOP_ACCEPTANCE 第39/40节。
