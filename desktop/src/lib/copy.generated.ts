// 由 design/copy.json 生成（python scripts/design_tokens.py），不要手改。
// iPhone 用同一份生成 MobileCore/Copy.swift（ADR-017）。

export const COPY = {
  /** 助理面板五段（Desktop 右侧面板 / iPhone 助理详情） */
  panel: {
    feed: "动态",
    goals: "目标",
    audit: "审核记录",
    schedule: "定时",
    identity: "身份",
  },
  /** 助理面板五段的说明 */
  panel_hint: {
    feed: "查看进展与待处理",
    goals: "查看进度和下一步",
    audit: "查看允许与拒绝",
    schedule: "查看计划和执行记录",
    identity: "资料、性格与记忆",
  },
  /** 会话入口（Desktop 侧栏 / iPhone 抽屉） */
  nav: {
    main_chat: "主要聊天",
    channels: "频道",
    waiting: "等你处理",
    teams: "任务小组",
    side_chats: "旁聊",
    settings: "设置",
    search: "搜索",
  },
  /** 审批类别 */
  approval_category: {
    read: "只读访问",
    own_data: "你的事项",
    draft: "本地草稿",
    write: "文件修改",
    execute: "命令执行",
    deliver: "对外发送",
    interact: "电脑或网页操作",
    irreversible: "不可逆操作",
  },
  /** 确认级别 */
  approval_risk: {
    normal: "普通确认",
    high: "加强确认",
  },
  /** 谁做的决定 */
  approval_source: {
    user: "人工确认",
    automatic_review: "后台审核",
    preauthorized: "既有授权",
    system: "系统决定",
    timeout: "超时",
    cancelled: "已取消",
  },
  /** 批准范围 */
  approval_scope: {
    once: "仅本次",
    turn: "本轮同类",
    existing_trust: "既有授权范围",
    none: "无新增授权",
  },
  /** 状态词（docs/DESIGN_SYSTEM.md 第 7 节） */
  status: {
    waiting: "等你",
    running: "在忙",
    done: "办好了",
    unknown: "无法确认",
    failed: "失败",
  },
  /** 个人空间频道 */
  channel: {
    create: "新建频道",
    name: "频道名称",
    topic: "频道说明（选填）",
    name_placeholder: "例如：产品想法",
    topic_placeholder: "这个频道用来聊什么？",
    scope_hint: "保存在当前空间。每个频道单独保存对话和输入草稿。",
    permission_hint: "频道说明只用于展示，不会授予权限，也不会自动分享给专员。",
    empty: "把一个话题留在这里，之后接着聊。",
    cancel: "取消",
    saving: "正在保存…",
    saved: "频道已保存",
    remove: "删除频道",
  },
} as const;

/** 字段缺失（missing）或值不认识（unknown）时显示的词。 */
export const COPY_FALLBACK = {
  approval_category: { missing: "类别待核对", unknown: "未分类操作" },
  approval_risk: { missing: "级别待核对", unknown: "级别待核对" },
  approval_source: { missing: "来源待核对", unknown: "来源待核对" },
  approval_scope: { missing: "范围待核对", unknown: "范围待核对" },
} as const;

/** 按服务端字段值取文案：不是字符串算缺失，不认识的值用 unknown。 */
export function copyLabel(group: keyof typeof COPY_FALLBACK, value: unknown): string {
  const table: Record<string, string> = COPY[group];
  if (typeof value !== "string") return COPY_FALLBACK[group].missing;
  return Object.prototype.hasOwnProperty.call(table, value) ? table[value] : COPY_FALLBACK[group].unknown;
}
