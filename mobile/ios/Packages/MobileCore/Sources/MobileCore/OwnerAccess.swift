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
        public let sources: [String: String]?
        public var unavailableLabels: [String] {
            (sources ?? [:]).filter { $0.value != "current" }.keys.sorted().map {
                ["audit": "审批审计", "app_cards": "回合审批", "codex": "Codex 动作", "dispatches": "任务单"][$0] ?? "未知来源"
            }
        }
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
        public let domainKind: String?
        public let sourceStatus: String?
        public let decisionMetadataMissing: Bool?
        public var recordLabel: String {
            switch recordType {
            case "card": return "卡片处理"
            case "card_wait": return "审批等待记录"
            case "decision": return "内核决定"
            case "dispatch": return "任务单"
            case "codex_action": return domainKind == "message" ? "Codex 消息" : domainKind == "interrupt" ? "Codex 叫停" : "Codex 动作"
            default: return "记录类型待核对"
            }
        }
        public var label: String { ["dispatch", "codex_action"].contains(recordType) ? recordLabel : ApprovalLabels.category(operationKind) }
        public var originLabel: String {
            ["app": "App", "runtime": "助理运行端", "im": "IM"][origin] ?? "入口待核对"
        }
        public var sourceStatusLabel: String? {
            guard let sourceStatus else { return nil }
            if recordType == "card_wait" {
                return sourceStatus == "pending" ? "当前卡片仍在等你确认，请到对话中处理"
                    : sourceStatus == "interrupted" ? "当前服务已无法接续旧卡，请先核对主聊天；不会重放批准或恢复执行"
                    : "原审批等待状态无法核对，请先检查主聊天"
            }
            let labels = recordType == "codex_action" ? [
                "proposed": "待批准", "approved": "已批准，等待交给 Codex", "sending": "正在交给 Codex，回执待核对",
                "sent": "Codex 已受理，执行结果仍需核对", "failed": "来源记录为未发送成功",
                "cancelled": "动作已撤回", "expired": "动作已过时", "unknown": "发送结果无法核对"
            ] : [
                "proposed": "待批准", "sending": "正在下发，结果待核对", "send_failed": "下发前失败，未提交",
                "submission_unknown": "下发结果无法核对", "running": "运行端报告进行中", "waiting": "运行端等待处理",
                "done": "运行端报告结束，验收仍需核对", "failed": "运行端报告失败", "cancelled": "任务单已取消", "stopped": "运行端已暂停或中断"
            ]
            return labels[sourceStatus] ?? "来源状态待核对"
        }
        public var outcomeLabel: String {
            switch approvalOutcome {
            case "interrupted": return "审批等待已中断"
            case "pending": return "仍待确认"
            case "expired": return "已过期，未放行"
            case "cancelled": return "已取消，未放行"
            case "approved": return "已允许"
            case "denied": return "已拒绝"
            case nil: return decision == "allowed" ? "已允许" : decision == "denied" ? "已拒绝" : "结果待核对"
            default: return "结果待核对"
            }
        }
        public var sourceLabel: String {
            ["user": "人工确认", "automatic_review": "后台审核", "preauthorized": "既有授权", "system": "系统决定", "timeout": "超时", "cancelled": "已取消"][decisionSource ?? ""] ?? "来源待核对"
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
