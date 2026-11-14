// 由 design/copy.json 生成（python scripts/design_tokens.py），不要手改。
// Desktop 用同一份生成 desktop/src/lib/copy.generated.ts（ADR-017）。

public enum VCCopy {
    /// 助理面板五段（Desktop 右侧面板 / iPhone 助理详情）
    public enum Panel {
        public static let feed = "动态"
        public static let goals = "目标"
        public static let audit = "审核记录"
        public static let schedule = "定时"
        public static let identity = "身份"
    }

    /// 助理面板五段的说明
    public enum PanelHint {
        public static let feed = "查看进展与待处理"
        public static let goals = "查看进度和下一步"
        public static let audit = "查看允许与拒绝"
        public static let schedule = "查看计划和执行记录"
        public static let identity = "资料、性格与记忆"
    }

    /// 会话入口（Desktop 侧栏 / iPhone 抽屉）
    public enum Nav {
        public static let mainChat = "主要聊天"
        public static let channels = "频道"
        public static let waiting = "等你处理"
        public static let teams = "任务小组"
        public static let sideChats = "旁聊"
        public static let settings = "设置"
        public static let search = "搜索"
    }

    /// 审批类别
    public enum ApprovalCategory {
        public static let read = "只读访问"
        public static let ownData = "你的事项"
        public static let draft = "本地草稿"
        public static let write = "文件修改"
        public static let execute = "命令执行"
        public static let deliver = "对外发送"
        public static let interact = "电脑或网页操作"
        public static let irreversible = "不可逆操作"
        public static let missing = "类别待核对"
        public static let unknown = "未分类操作"
        public static let table: [String: String] = ["read": "只读访问", "own_data": "你的事项", "draft": "本地草稿", "write": "文件修改", "execute": "命令执行", "deliver": "对外发送", "interact": "电脑或网页操作", "irreversible": "不可逆操作"]
        /// 按服务端字段值取文案：没有这个字段用 missing，不认识的值用 unknown。
        public static func label(_ value: String?) -> String {
            guard let value else { return missing }
            return table[value] ?? unknown
        }
    }

    /// 确认级别
    public enum ApprovalRisk {
        public static let normal = "普通确认"
        public static let high = "加强确认"
        public static let missing = "级别待核对"
        public static let unknown = "级别待核对"
        public static let table: [String: String] = ["normal": "普通确认", "high": "加强确认"]
        /// 按服务端字段值取文案：没有这个字段用 missing，不认识的值用 unknown。
        public static func label(_ value: String?) -> String {
            guard let value else { return missing }
            return table[value] ?? unknown
        }
    }

    /// 谁做的决定
    public enum ApprovalSource {
        public static let user = "人工确认"
        public static let automaticReview = "后台审核"
        public static let preauthorized = "既有授权"
        public static let system = "系统决定"
        public static let timeout = "超时"
        public static let cancelled = "已取消"
        public static let missing = "来源待核对"
        public static let unknown = "来源待核对"
        public static let table: [String: String] = ["user": "人工确认", "automatic_review": "后台审核", "preauthorized": "既有授权", "system": "系统决定", "timeout": "超时", "cancelled": "已取消"]
        /// 按服务端字段值取文案：没有这个字段用 missing，不认识的值用 unknown。
        public static func label(_ value: String?) -> String {
            guard let value else { return missing }
            return table[value] ?? unknown
        }
    }

    /// 批准范围
    public enum ApprovalScope {
        public static let once = "仅本次"
        public static let turn = "本轮同类"
        public static let existingTrust = "既有授权范围"
        public static let none = "无新增授权"
        public static let missing = "范围待核对"
        public static let unknown = "范围待核对"
        public static let table: [String: String] = ["once": "仅本次", "turn": "本轮同类", "existing_trust": "既有授权范围", "none": "无新增授权"]
        /// 按服务端字段值取文案：没有这个字段用 missing，不认识的值用 unknown。
        public static func label(_ value: String?) -> String {
            guard let value else { return missing }
            return table[value] ?? unknown
        }
    }

    /// 状态词（docs/DESIGN_SYSTEM.md 第 7 节）
    public enum Status {
        public static let waiting = "等你"
        public static let running = "在忙"
        public static let done = "办好了"
        public static let unknown = "无法确认"
        public static let failed = "失败"
    }

    /// 个人空间频道
    public enum Channel {
        public static let create = "新建频道"
        public static let name = "频道名称"
        public static let topic = "频道说明（选填）"
        public static let namePlaceholder = "例如：产品想法"
        public static let topicPlaceholder = "这个频道用来聊什么？"
        public static let scopeHint = "保存在当前空间。每个频道单独保存对话和输入草稿。"
        public static let permissionHint = "频道说明只用于展示，不会授予权限，也不会自动分享给专员。"
        public static let empty = "把一个话题留在这里，之后接着聊。"
        public static let cancel = "取消"
        public static let saving = "正在保存…"
        public static let saved = "频道已保存"
        public static let remove = "删除频道"
    }

    /// 角色家族形象
    public enum Characters {
        public static let general = "总助手"
        public static let research = "研究助手"
        public static let development = "开发助手"
        public static let calendar = "日程助手"
        public static let personalSpace = "我的个人空间"
    }

    /// 统一角色入口与资料
    public enum Role {
        public static let list = "角色"
        public static let profile = "角色资料"
        public static let add = "添加角色"
        public static let refresh = "刷新角色"
        public static let close = "关闭角色资料"
        public static let settings = "设置"
        public static let access = "工具与权限"
        public static let memory = "记忆"
        public static let activity = "工作记录"
        public static let active = "已启用"
        public static let paused = "已暂停"
        public static let retired = "已撤掉"
        public static let busy = "在忙"
        public static let unknown = "岗位待核对"
        public static let unverified = "名录待核对"
        public static let sourceChanged = "运行端或连接已变化，当前资料仅供回查。请关闭后重新打开角色资料核对。"
        public static let manage = "角色与权限"
        public static let manageHint = "查看已批准的角色与权限方案，核对待批提议。"
    }
}
