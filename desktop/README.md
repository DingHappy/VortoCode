# VortoCode Desktop

VortoCode Desktop 是当前默认产品与首要分发入口，也是现有 Gateway 的本机 Agent 工作台；它不包含第二套 Agent runtime。它通过冻结的三端协议连接 `vc server`，复用 CLI/TUI/Web 已有的会话、权限门、任务与通知能力。CLI 保留为高级与自动化入口，Web 留作后续团队/托管入口。

## 当前能力

- 启动后自动进入可直接对话的 General 无目录会话；只有任务确实需要文件时才由结构化事件请求 Scratch 或 Git 项目，不再在发送首条消息时强制选择目录
- General / Scratch / Project 三种明确范围：General 无文件、Shell、Git；Scratch 使用应用管理的按会话隔离 Git 目录；Project 只来自用户明确选择的真实 Git 根目录
- Plan / Build 对话与流式事件
- 会话列表、切换、重命名与删除
- 人工确认、tainted 回合提示与结构化 diff
- Goal 合同、结构化验收证据、失败重试与断点续跑
- terminal/test/preview 运行台账、隔离 worktree 后台任务、暂停恢复与交接；会话、任务、DevPlan、分支和实时 worktree 可稳定互相定位
- Git working/staged 与 hunk 审查、行级评论修复、reviewed commit、Draft PR 与 CI 失败证据
- Git baseline、HEAD、逻辑 index 和来源变化实时推送，Desktop 自动刷新权威 diff，过期 hunk 操作继续 fail closed
- 后台任务的持久 `vorto/*` 分支审查：运行中 worktree 只读，完成后按稳定 hunk 接受/撤销；撤销会生成审查提交，隔离重验通过前阻止 Draft PR
- 可选团队 hunk 策略：项目可要求全部当前文本 hunk 完成精确接受；任务卡展示 accepted/total/pending，Draft PR 由服务端在交付时实时重验
- 多项目/多 Scratch runtime 常驻、按项目会话、后台继续执行、精确监督与异常恢复
- 跨项目 Inbox：统一聚合每个本机 runtime 的待确认、Hook、执行中会话、Goal 与后台任务；单项目失联独立降级，并可直接切到对应项目/会话处理
- 模型服务快捷设置：VortoCode Relay、自定义 OpenAI 兼容网关与本机回环模型服务；Base URL、模型名和 Key 由原生层管理，Key 只存 macOS Keychain，保存后重启当前 runtime 生效
- 项目记忆安全投影/确认写入，以及版本化 Artifact 沙箱预览和 Agent 迭代
- runtime 状态、决策中心、共享审计、Project Journal 与显式授权系统通知
- Hook 失败、超时与阻止事项汇总到 Dashboard/决策中心，可定位会话并安全标记已查看
- Hook 信任预览展示调用级有效能力；仓库 allowlist 只能缩减，独立事件快照和被动只读边界阻止 Hook 接管 Agent 主循环
- Git scope 源码文件搜索、预览和受确认的轻量编辑
- 选择整文件或最多 500 行范围，合计最多 8 个引用加入下一轮 Agent 上下文
- 从选中行跳转到 VS Code/Cursor；CLI 不可用时降级到系统默认应用
- 内嵌 UTF-8 编辑缓冲区、dirty 状态、Tab/⌘S、CRLF/LF 保持与安全保存

产品边界和后续编辑能力见 [`../docs/DESKTOP_PRODUCT_SPEC.md`](../docs/DESKTOP_PRODUCT_SPEC.md)。

## 开发

需要 Node.js、Rust、源码测试所需的 CPython 3.10+ 和当前平台的 Tauri 2 系统依赖。PyInstaller 与 core Gateway 依赖由仓库内的锁文件管理；发布 sidecar 使用仓库固定、SHA-256 校验的 standalone CPython，不要求或信任全局 Python site-packages。

```bash
cd desktop
npm install
npm run tauri dev
```

也可以先在浏览器壳中验证静态界面：

```bash
npm run dev
```

浏览器壳无法执行 runtime 进程管理命令；完整连接应使用 Tauri 窗口。

## 检查与打包

```bash
cd desktop
npm run check
npm run sidecar:python
npm run sidecar:smoke
npm run bundle:app
npm run smoke:bundle
npm run bundle:dmg
npm run release:verify -- --mode preview
```

也可以用 `npm run bundle:preview` 一次完成 sidecar smoke、DMG、app、GUI smoke 和 preview 发布证据。

### macOS 稳定开发签名

不要用普通 `bundle:app` 生成的 unsigned 包反复覆盖 `/Applications/VortoCode.app`：macOS
Keychain 会把二进制内容变化视为新的调用方，已经选择的“始终允许”无法稳定复用。使用一次性 setup
在临时目录生成长期有效的 `VortoCode Development` 代码签名证书并导入登录钥匙串；导入完成后临时
密钥文件会立即删除，私钥只保留在 Keychain，并只授权系统 `codesign` 使用：

```bash
cd desktop
npm run signing:setup
npm run signing:doctor
npm run bundle:dev-signed
npm run install:dev-signed
npm run verify:installed
```

`signing:setup` 默认使用 `VortoCode Development`；若需要自定义名称，可在 setup 和后续命令中统一
设置 `VORTOCODE_CODESIGN_IDENTITY`。`signing:doctor` 要求 Keychain 中存在有效的非 ad-hoc 身份；
只有一个有效身份时可以自动选择，多个身份时必须显式指定。签名构建会先冻结并 smoke sidecar，再让 Tauri 使用同一身份签名嵌套 runtime 和主
app，最后验证 bundle identifier、严格签名和 designated requirement。上一次签名证据或已安装 app
的 designated requirement 不一致时安装会 fail closed；只有证书有计划轮换时才可临时设置
`VORTOCODE_ALLOW_SIGNING_IDENTITY_CHANGE=1`。

从旧 unsigned 包第一次切换到稳定签名包时，读取既有模型配置仍可能要求最后一次授权。之后同一证书、
`com.vorto.vortocode` identifier 和 Keychain service 必须保持稳定。Desktop 原生层在单次进程生命周期
只读取一次模型配置；General、Project、设置页和并发 runtime 复用同一内存缓存，保存/清除成功后同步
更新缓存，授权失败不会被缓存。

本机自签名证书只用于开发体验，不能替代公开分发所需的 Developer ID Application 签名与公证。

`sidecar:python` 根据 [`managed-python-sources.json`](./managed-python-sources.json) 下载当前 target triple 的固定 standalone CPython full archive，先验证大小和 SHA-256，再验证 `PYTHON.json`、架构、版本、PGO/LTO 构建选项、许可证目录与完整运行时树哈希。当前固定 macOS x86_64 和 arm64 资产；缓存位于 `src-tauri/target/managed-python/`，不会进入 Git。

`sidecar:build` 使用 `requirements-runtime.lock` 在 `src-tauri/target/sidecar-python/<target-triple>` 创建独立 venv，并要求实际 `pip freeze` 与锁文件完全一致；首次同步需要访问 Python 包索引，之后输入、锁、来源配置和 Python 运行时未变化时复用缓存。`requirements-runtime.in` 只保存人工维护的直接依赖，更新后必须在每个受支持平台重新解析并审查完整 lock diff。
它只暴露固定的本机 Gateway server 入口，不复制完整 `vc` CLI。生成的二进制与构建缓存不会进入 Git。

macOS 产物位于 `src-tauri/target/release/bundle/macos/VortoCode.app` 和
`src-tauri/target/release/bundle/dmg/VortoCode_<version>_<arch>.dmg`。`sidecar:smoke` 会在随机本机端口验证冻结 runtime 的 health 和 Web 资源；
`smoke:bundle` 会启动真实 release bundle，并同时验证 React、Tauri IPC 以及安装包内 sidecar 的定位和启动。冻结完成后还会根据 PyInstaller 实际 Analysis 生成 `THIRD_PARTY_NOTICES.txt` 和结构化依赖清单，覆盖实际冻结的 Python distributions 与 standalone runtime 自带的全部许可证；许可证证据缺失会直接中止构建，notices 会随 app 一起打包。

`release:verify` 检查 target architecture、sidecar/notices 哈希、DMG、Git provenance、代码签名、公证票据和 Gatekeeper，并写入 `src-tauri/target/release/release-evidence/`。`preview` 模式允许未签名但明确记录 warning；`--mode release` 对任一正式发布闸门 fail closed。

当前开发者预览包已经内置 core Gateway runtime，不要求用户预装 Python 或 `vc`。Desktop 启动即自动连接 General；普通对话、研究、规划和 Artifact 不绑定目录。Agent 只有在确实需要文件时才能发出结构化 `workspace_required`，用户可以审阅原因和待继续任务后创建 Scratch 或选择 Git 项目；切换后的新会话不会继承 General 的外部内容/凭据上下文，待继续任务只回填输入框供再次确认。范围切换会自动完成启动、health 等待和协议连接；首选端口被占用时自动分配其他回环端口。启动顺序是内置 runtime、系统 `vc`，然后仅在 VortoCode 开发 checkout 中尝试源码 Python 回退；
用户仍可连接自行运行的本机 `vc server` 以使用未冻结的可选依赖。x86_64 sidecar 已使用受管 standalone CPython 构建；arm64 来源资产可在 x86_64 主机上预取和静态验真，但 sidecar、Tauri app 和 DMG 必须在 Apple Silicon 上用该架构的原生 wheels 重新构建和验收，不能把静态资产检查当成 arm64 成品。公开分发还需要人工复核 notices、Apple Developer ID 签名、公证与发布凭据，仓库不会伪造这些步骤。

## 安全边界

- V0 只接受 `127.0.0.1` 和 `localhost`。
- API Token 只保存在 Desktop 进程内，不写入 localStorage。
- LLM API Key 不写入 localStorage、项目 `.env` 或 runtime 恢复注册表；Desktop 用 macOS Keychain 保存完整模型服务配置，在单次 app 进程中只读取一次并只把它注入新启动的子 runtime 环境。远程 Base URL 强制 HTTPS，本机 HTTP 只接受 `127.0.0.1` / `localhost`。
- Desktop 只能以固定参数启动内置 Gateway 或 `vc server`；内置 sidecar 只接受 `server`、回环 host 和 1024–65535 端口，首选端口不可用时由系统分配空闲回环端口。项目路径必须归一化为真实 Git 顶层目录，只有 VortoCode 自身开发 checkout 允许固定的 `python -m src.cli` 回退。
- General 的 Agent 工具面和 REST 面同时 fail closed：联网检索、会话、决策、Journal 与 Artifact 可用；文件/命令/任务 API 返回结构化 `workspace_required`。不能用最近项目、HOME 或应用数据目录作为隐式项目。
- Scratch 位于 Desktop app-data 下的 `workspaces/scratch/<session-id>` 并独立初始化 Git；General 的内部持久化目录不会在状态快照中作为 workdir 暴露。
- 源码列表只使用 Git tracked/untracked 且未忽略的文件；读取会 canonicalize 并拒绝越界、软链逃逸、二进制和超过 1 MiB 的文本。
- Desktop 只发送文件/行范围引用，Gateway 在共享 `read_file` 能力门内读取正文；外部编辑器只走固定 argv，不执行 shell 或 `$EDITOR`。
- 内嵌编辑保存只发送结构化 `workspace_edit`；Gateway 负责 Git scope 重验、permissions/能力检查、diff、人工确认、
  双重 SHA-256 冲突检测与同目录原子替换，Tauri WebView 没有任意写文件命令。
- Gateway stdout/stderr 按 runtime 写入 Desktop 应用配置目录下的 `logs/runtime-<runtime-id>.log`，不因启动 General 而污染用户目录。
- runtime 恢复注册表按稳定 runtime id 保存 scope、项目/Scratch 身份、工作区位置、本机地址、时间和进程线索，不保存 token；旧单记录会自动迁移，异常重启不会根据旧 PID 自动杀进程或执行任务。
- 切换项目只断开当前 WebSocket 观察端，不停止原 runtime 或其 session actor；最多同时托管 12 个 runtime。正常退出 Desktop 时会终止它启动的所有 runtime 进程组（包括 PyInstaller one-file 派生的 Python server）；连接到外部已有 runtime 时不会接管其生命周期。停止失败时保留对应恢复记录，避免把仍在运行的进程遗忘。
