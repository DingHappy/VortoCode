import Foundation

// 任务小组与旁聊（ADR-017 M3）：经手机网关转给常驻助理读写。字段名和 Desktop 的 /api/teams 一致（下划线转驼峰）。

public struct TeamMemberRef: Codable, Sendable, Equatable {
    public let name: String
    public let role: String
}

/// 抽屉里的一行。
public struct TeamSummary: Codable, Identifiable, Sendable, Equatable {
    public let id: String
    public let objective: String
    public let status: String
    public let mode: String?
    public let waitingReason: String?
    public let project: String?
    public let tainted: Bool
    public let updatedAt: String?
    public let members: [TeamMemberRef]?

    /// 进行中（在做、等你回复、等你验收）
    public var isLive: Bool { ["active", "waiting", "review"].contains(status) }
    /// 要主人处理：交付了等验收，或在等主人回答（和 Desktop 的 waitingTeams 一样）
    public var needsOwner: Bool { status == "review" || status == "waiting" }
    public var statusLabel: String { TeamStatus.label(status) }
}

public enum TeamStatus {
    public static func label(_ status: String) -> String {
        switch status {
        case "active": return VCCopy.Status.running
        case "waiting": return "需要你回复"
        case "review": return "交付了，等你验收"
        case "done": return VCCopy.Status.done
        case "cancelled": return "已停止"
        case "failed": return VCCopy.Status.failed
        default: return VCCopy.Status.unknown
        }
    }
}

public struct TeamList: Codable, Sendable { public let teams: [TeamSummary] }

/// 一个小组的群聊。
public struct TeamChat: Codable, Sendable, Equatable {
    public struct Team: Codable, Sendable, Equatable {
        public let id: String
        public let objective: String
        public let status: String
        public let mode: String?
        public let waitingReason: String?
        public let project: String?
        public let tainted: Bool?
        public let acceptance: [String]?
        public let updatedAt: String?
        public var isLive: Bool { ["active", "waiting", "review"].contains(status) }
    }
    public struct Member: Codable, Sendable, Equatable {
        public let name: String
        public let role: String
        public let title: String?
        public let state: String?
        public let rounds: Int?
        public let delivered: Int?
        public let failures: Int?
        public var working: Bool { state == "working" }
    }
    public struct Evidence: Codable, Sendable, Equatable {
        public let criterion: String?
        public let text: String?
        public let evidence: String?
    }
    public struct Message: Codable, Sendable, Equatable {
        /// owner / coordinator / member / system
        public let speaker: String
        public let kind: String
        public let at: String?
        public let member: String?
        public let mention: String?
        public let text: String
        public let tainted: Bool?
        public let evidence: [Evidence]?
        public var fromOwner: Bool { speaker == "owner" }
        public var isSystem: Bool { speaker == "system" }
        /// 气泡上方的名字
        public var speakerLabel: String {
            switch speaker {
            case "owner": return "你"
            case "coordinator": return "协调人"
            case "member": return member ?? "成员"
            default: return ""
            }
        }
    }
    public let team: Team
    public let members: [Member]
    public let messages: [Message]
}

/// 小组写操作的回执：只回小组此刻的状态，界面随后重读群聊。
public struct TeamOpResult: Codable, Sendable { public let team: TeamChat.Team }

/// 旁聊（Desktop 上开的其它会话）：手机上只读。
public struct SideChatSummary: Codable, Identifiable, Sendable, Equatable {
    public let sid: String
    public let title: String
    public let messages: Int
    public let updatedAt: String?
    public var id: String { sid }
}

public struct SideChatList: Codable, Sendable { public let chats: [SideChatSummary] }

public struct SideChatTranscript: Codable, Sendable, Equatable {
    public let sid: String
    public let title: String
    public let items: [ChatItem]
}
