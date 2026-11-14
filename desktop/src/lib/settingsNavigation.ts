export const SETTINGS_SECTIONS = [
  { id: "account", label: "账号与用量", description: "账号、会员与模型用量，集中查看。", scope: "账号状态来自 VortoOne；模型额度以服务端返回为准。" },
  { id: "model", label: "模型与执行", description: "选择模型服务，安排研究、实现与审查的分工。", scope: "保存在这台 Mac。模型与执行者配置不自动同步到常驻实例。" },
  { id: "computer", label: "电脑与连接", description: "管理本机常驻助理，或连接其他电脑。", scope: "常驻助理、远程画面和临时本机引擎分别运行，各自报告状态。连接画面不会授予助理操作权限。" },
  { id: "permissions", label: "权限与自动化", description: "决定助手能做什么，以及哪些操作需要你确认。", scope: "授权级别属于当前工作区；浏览器与桌面开关配置在本机。自动化仍遵守各自的能力限制。" },
  { id: "staff", label: "团队与权限", description: "查看专员的岗位与权限，批准登记，暂停或撤掉专员。", scope: "登记保存在常驻助理。核对岗位权限和完整内容后，由主人确认批准。" },
  { id: "tools", label: "工具与集成", description: "为助手连接工具，检查项目加载的扩展。", scope: "MCP 按其配置加载；规则、Skills 与 Hooks 展示当前项目的状态。" },
  { id: "personal", label: "个性化", description: "调整助手资料、全局指令和界面外观。", scope: "外观在本机立即生效；本机全局指令影响同一用户目录下的新会话。助手资料在对应实例中管理。" },
  { id: "advanced", label: "高级与关于", description: "更新、版本与配置文件。", scope: "这里管理这台 Mac 上的客户端和本机配置文件。" },
] as const;

export type SettingsPage = typeof SETTINGS_SECTIONS[number]["id"];
/** 保留旧入口兼容，新的入口应直接指向明确的分类。 */
export type SettingsSection = SettingsPage | "abilities";
export function settingsPage(section: SettingsSection): SettingsPage {
  return section === "abilities" ? "permissions" : section;
}

export const SETTINGS_OWNER_LABELS = { desktop: "这台 Mac", workspace: "当前工作区", space: "个人空间", project: "项目" } as const;
export type SettingsOwner = keyof typeof SETTINGS_OWNER_LABELS;
export const SETTINGS_ITEMS: Array<{ id: string; page: SettingsPage; label: string; keywords: string; owner: SettingsOwner }> = [
  { id: "account", owner: "desktop", page: "account", label: "VortoOne 账号与会员", keywords: "登录 退出 注册 套餐 额度 token plan quota usage 用量 计费" },
  { id: "mobile-account", owner: "space", page: "account", label: "手机与常驻助手", keywords: "手机 同账号 关联 解除 配对 移动端" },
  { id: "services", owner: "desktop", page: "model", label: "模型服务与 API Key", keywords: "供应商 omlx 本地 端侧 openai deepseek kimi glm minimax relay https 默认 地址" },
  { id: "roles", owner: "desktop", page: "model", label: "模型与执行者分工", keywords: "研究 实现 审查 评判器 codex claude cli 核心 子 agent 实验" },
  { id: "routing", owner: "desktop", page: "model", label: "自动调度与常用模型", keywords: "快速 强力 名单 picks 智能" },
  { id: "local-resident", owner: "desktop", page: "computer", label: "本机常驻助理", keywords: "本机 Mac 常驻 后台 登录 开机 安装 更新 模型 退出" },
  { id: "resident", owner: "desktop", page: "computer", label: "其他电脑上的常驻助理", keywords: "常驻助手 连接 远程 服务器 24小时 实例 服务" },
  { id: "desktop", owner: "desktop", page: "computer", label: "远程桌面连接", keywords: "ssh vnc 观察 接管 画面 密码库 持久化" },
  { id: "runtime", owner: "desktop", page: "computer", label: "本机引擎与工作区", keywords: "项目 scratch runtime 恢复 gateway token 连接" },
  { id: "trust", owner: "workspace", page: "permissions", label: "工作区授权", keywords: "确认 只读 完全信任 trust 权限" },
  { id: "browser", owner: "desktop", page: "permissions", label: "浏览器操作", keywords: "网页 chrome 浏览器 自动化" },
  { id: "control", owner: "desktop", page: "permissions", label: "桌面操作权限", keywords: "电脑 截屏 窗口 点击 输入 应用 computer use" },
  { id: "schedule", owner: "space", page: "permissions", label: "定时任务", keywords: "计划 自动化 打卡 提醒 cron schedule" },
  { id: "staff", owner: "space", page: "staff", label: "专员与岗位登记", keywords: "团队 专员 岗位 权限 批准 确认 暂停 撤掉" },
  { id: "codex", owner: "desktop", page: "tools", label: "Codex 动态观察", keywords: "助理 查看 任务 进展" },
  { id: "extensions", owner: "workspace", page: "tools", label: "MCP 工具服务", keywords: "插件 工具 集成" },
  { id: "project-extensions", owner: "project", page: "tools", label: "项目规则、Skills 与 Hooks", keywords: "项目 扩展 skills hooks 规则 信任" },
  { id: "identity", owner: "space", page: "personal", label: "助手资料", keywords: "助理 名字 性格 soul 关于你" },
  { id: "instructions", owner: "desktop", page: "personal", label: "全局指令", keywords: "agents.md 自定义 instructions" },
  { id: "appearance", owner: "desktop", page: "personal", label: "外观主题", keywords: "深色 浅色 系统 配色" },
  { id: "version", owner: "desktop", page: "advanced", label: "更新与版本", keywords: "构建 升级 关于 build" },
  { id: "files", owner: "desktop", page: "advanced", label: "配置文件", keywords: "路径 finder 文件夹" },
];

export function searchSettings(query: string) {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  return SETTINGS_ITEMS.filter(item => {
    const page = SETTINGS_SECTIONS.find(page => page.id === item.page)!;
    const text = `${item.label} ${item.keywords} ${page.label} ${SETTINGS_OWNER_LABELS[item.owner]}`.toLowerCase();
    return words.every(word => text.includes(word));
  });
}
