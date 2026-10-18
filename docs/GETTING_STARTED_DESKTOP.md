# 只用 Desktop 上手

给想直接用 VortoCode Desktop、不打算先搭服务的人。读完能做到：装好 App、连上模型、知道单机能做什么，以及什么时候才需要一台常驻电脑。

## 两种用法

| | 只用 Desktop（本文） | Desktop + 常驻电脑 |
|---|---|---|
| 需要什么 | 一台 Mac | 再加一台一直开着的机器（云主机、NAS、家里的服务器，或让这台 Mac 兼任） |
| 能做什么 | 对话、读写代码、隔离开发流水线（worktree → 自测 → 基线对比 → 落分支 → 开 PR）、后台任务、插件（MCP / 技能 / 精选目录）、浏览器操控、个人资料与记忆 | 以上全部，外加定时任务、早晚报、推送到手机、从手机派活、小智语音 |
| 什么时候在工作 | **App 开着、Mac 醒着时** | 常驻那台一直在跑，合上笔记本也照常 |

先只用 Desktop 是完整可用的：核心的开发流水线不打折扣。等你发现「我想让它在我不在的时候也干活」，再看文末的常驻部分。

## 1. 获取 App

目前**没有发布安装包**，需要从源码构建一次（约 5–10 分钟，第一次要联网）。

需要：macOS 10.15+、git、Node.js（含 npm）、Rust 工具链（`rustup` 装好即可）。Python **不用自己装**：构建脚本会下载一份固定版本的 CPython，并按 sha256 校验后打进 App。

```bash
git clone https://github.com/DingHappy/VortoCode.git
cd VortoCode/desktop
npm ci
npm run bundle:app
```

产物在 `desktop/src-tauri/target/release/bundle/macos/VortoCode.app`，拖进「应用程序」即可。构建会顺带跑一次内置引擎的启动自检，失败会直接报错，不会给你一个打不开的 App。

> 这样构建出来的 App 是**本地签名（ad-hoc）**，只适合自己这台机器用。要分发给别人需要 Apple Developer ID 签名与公证，见 `npm run signing:setup` 与 `docs/PUBLIC_RELEASE_CHECKLIST.md`。

## 2. 第一次打开：连上模型

首页会提示「还没有配置模型服务」，点「连接模型」，在「模型服务」里选一家并填 Key：

| 预设 | 说明 |
|---|---|
| OpenAI / DeepSeek / Kimi / 智谱 GLM / 千问 / MiniMax | 各家官方接口，用你自己的 Key |
| 本机模型（Ollama，`http://127.0.0.1:11434/v1`）/ oMLX | 不需要 Key；见 `docs/LOCAL_MODELS.md` |
| 自定义 | 任何 OpenAI 兼容地址；远程地址必须是 HTTPS |

填完点「测试连接」确认能通。Key 保存在本机的 `~/Library/Application Support/com.vorto.vortocode/llm-profile.json`（只有你自己可读写，权限 600），不进任何仓库。

> 本机小模型能聊天，但开发流水线依赖稳定的工具调用和多步规划，小模型经常跟不上。先用一家云端模型把流程跑通，再按需要换。

## 3. 开始用

- **打开项目**：选一个 git 仓库。对话里让它改代码时，改动先在隔离的 worktree 里实现和自测，绿了才落到 `vorto/*` 分支，不碰你的 main 和工作区。
- **通用会话**：不选项目也能聊天、整理事情、起草文字；这里没有文件、Shell 和 Git。
- **插件页**：「精选目录」里可以一键装几个免凭据的 MCP 服务（DeepWiki、Context7 等）和通用技能；「MCP 服务」会告诉你每个服务会不会真的启动、为什么。
- **侧边栏**：会话按「等你处理 / 进行中 / 其他」分组，先看停下来等你的那几个。

## 单机时不会发生的事（以免误会）

- **定时任务不会按时跑**：只用 Desktop 时本机调度是关着的。你让助理「每天 8 点提醒我」，它会建好作业，但会明说「这台机器上不会自动跑」；可以手动跑一次。
- **没有手机推送、早晚报**：没有地方可推。Desktop 开着时的系统通知可以在「待处理」里打开。
- **合上电脑就停**：后台任务跟着 App 和 Mac 一起停，重新打开后可以在任务看板里恢复。

## 想让它 7×24 干活：常驻电脑

常驻电脑不是另一个产品，是**同一个程序以服务方式一直跑着**（`vc server`，打开调度、配上 IM）。可以是：

1. 一台便宜的云主机或家里一直开着的机器（推荐，效果最好）；
2. 这台 Mac 自己：用 launchd 常驻，但 Mac 睡眠或合盖时会停。

部署步骤（macOS launchd / Linux systemd、Telegram 配对、开关矩阵、远程访问的鉴权边界）见 [`docs/OPS.md`](OPS.md)。部署好后，在 Desktop 助理模式里点「连接常驻电脑」，填它的地址和 `VORTOCODE_API_TOKEN`。
