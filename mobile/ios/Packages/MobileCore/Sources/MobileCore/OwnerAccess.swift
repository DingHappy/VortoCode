import Foundation

/// 服务端的只读投影；查看或解码这些字段不授予权限。
public struct OwnerAccessSnapshot: Codable, Sendable, Equatable {
    public let schemaVersion: Int
    public let generatedAt: String
    public let permissions: Permissions
    public let history: History
    public struct Permissions: Codable, Sendable, Equatable {
        public let profile: String
        public let storedLevel: String
        public let effectiveLevel: String
        public let ceiling: String
        public let scopes: [String]
        public let taintRevokesPreauthorization: Bool
        public let categories: [Category]
    }
    public struct Category: Codable, Identifiable, Sendable, Equatable {
        public let operationKind: String
        public let risk: String
        public let requiredKey: String
        public let preauthorized: Bool
        public var id: String { operationKind }
        public var label: String { ApprovalLabels.category(operationKind) }
    }
    public struct History: Codable, Sendable, Equatable {
        public let entries: [Entry]
        public let status: String
        public let bounded: Bool
    }
    public struct Entry: Codable, Identifiable, Sendable, Equatable {
        public let id: String
        public let ts: String
        public let operationKind: String?
        public let risk: String?
        public let requiredKey: String?
        public let approvalScope: String?
        public let decisionSource: String?
        public let approvalOutcome: String?
        public let decision: String
        public let tainted: Bool
        public let recordType: String
        public let origin: String
        public var label: String { ApprovalLabels.category(operationKind) }
        public var outcomeLabel: String {
            switch approvalOutcome {
            case "expired": return "已过期，未放行"
            case "cancelled": return "已取消，未放行"
            case "approved": return "已允许"
            case "denied": return "已拒绝"
            case nil: return decision == "allowed" ? "已允许" : "已拒绝"
            default: return "结果待核对"
            }
        }
        public var sourceLabel: String {
            ["user": "人工确认", "preauthorized": "既有授权", "system": "系统决定", "timeout": "超时", "cancelled": "已取消"][decisionSource ?? ""] ?? "来源待核对"
        }
        public var scopeLabel: String {
            ["once": "仅本次", "existing_trust": "既有授权范围", "none": "无新增授权"][approvalScope ?? ""] ?? "范围待核对"
        }
    }
    public func validate() throws {
        guard schemaVersion == 1, permissions.profile == "owner_device",
              ["ask", "reads"].contains(permissions.effectiveLevel), permissions.scopes == ["once"],
              permissions.taintRevokesPreauthorization, history.bounded,
              permissions.categories.count <= 16, Set(permissions.categories.map(\.id)).count == permissions.categories.count,
              history.entries.count <= 100, Set(history.entries.map(\.id)).count == history.entries.count else { throw MobileError.unavailable }
    }
}

public enum ApprovalLabels {
    public static func category(_ kind: String?) -> String {
        ["read": "只读访问", "own_data": "你的事项", "draft": "本地草稿", "write": "文件修改",
         "execute": "命令执行", "deliver": "对外发送", "interact": "电脑或网页操作", "irreversible": "不可逆操作"][kind ?? ""] ?? "类别待核对"
    }
}
