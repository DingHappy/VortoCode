import Foundation

/// 原生 bootstrap：这台设备的授权身份，以及此刻能做的写入（服务端开了写入且已绑定钥匙才有）。
public struct NativeBootstrap: Codable, Sendable {
    public struct Device: Codable, Sendable { public let id: String; public let name: String }
    public struct Capabilities: Codable, Sendable { public let reads: [String]; public let writes: [String] }
    public let schemaVersion: Int
    public let instanceId: String
    public let device: Device
    public let capabilities: Capabilities
    public func validate(instance: String) throws {
        guard schemaVersion == 1, instanceId == instance,
              device.id.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil else { throw MobileError.unauthorized }
    }
    public var canWrite: Bool { capabilities.writes.contains("capture") }
}

/// 等你批准的 Codex 动作。批准时把 `actionDigest` 原样签进去：服务端核对它仍是此刻的动作。
public struct PendingDecision: Codable, Identifiable, Sendable, Equatable {
    public let id: String
    public let kind: String
    public let thread: String
    public let text: String
    public let tainted: Bool
    public let createdAt: String?
    public let actionDigest: String
    public var title: String { kind == "interrupt" ? "叫停这一轮" : "发给 Codex" }
}

/// 「我替你做了什么」里的一条改动。
public struct OwnerChange: Codable, Identifiable, Sendable, Equatable {
    public let id: String
    public let ts: String
    public let summary: String
    public let status: String
    public let tainted: Bool
    public let source: String?
    public let undoDetail: String?
    public let notRecallable: [String]?
    public var statusLabel: String {
        ["done": "可撤销", "undoing": "正在撤销", "undone": "已撤销", "failed": "撤销没成功，请核对"][status] ?? "待核对"
    }
}

public struct UndoOutcome: Codable, Sendable {
    public let ok: Bool
    public let detail: String
    public let notRecallable: [String]
}

public struct DecisionOutcome: Codable, Sendable {
    public let id: String
    public let status: String
    public let statusText: String
}

/// 绑定钥匙的回执。手机入口在公网上：第一台手机直接生效，之后的新手机要在 Desktop 上对核对码批准。
public struct KeyBinding: Codable, Sendable {
    public let pending: Bool?
    public let code: String?
    public var waitingApproval: Bool { pending == true }
}

public struct CaptureOutcome: Codable, Sendable {
    public let goalId: String
    public let objective: String
}

/// 助理拟好、等你下发的任务单。下发要 Face ID，签进去的 `digest` 必须对上此刻的标题和目标。
/// 读过外部内容后拟的（`tainted`）只能在电脑上下发。
public struct DispatchProposal: Codable, Identifiable, Sendable, Equatable {
    public let id: String
    public let project: String?
    public let title: String
    public let goal: String
    public let status: String
    public let tainted: Bool
    public let criteria: [String]
    public let digest: String
}

/// 主要聊天里的一条（Desktop、Telegram、App 都在这一段，`source` 标来源）。
public struct ChatItem: Codable, Sendable, Equatable {
    public let role: String
    public let text: String
    public let rid: String?
    public let source: String?
    public let at: String?
    public init(role: String, text: String, rid: String? = nil, source: String? = nil, at: String? = nil) {
        self.role = role; self.text = text; self.rid = rid; self.source = source; self.at = at
    }
    public var isMine: Bool { role == "user" }
    /// 不是这台手机发的就标一下从哪来的
    public var sourceLabel: String? {
        switch source {
        case "app", nil: return nil
        case let value? where value.hasPrefix("telegram"): return "Telegram"
        case let value? where value.hasPrefix("dingtalk"): return "钉钉"
        default: return "电脑"
        }
    }
}

public struct ChatHistory: Codable, Sendable {
    public let registered: Bool
    public let items: [ChatItem]
}

/// 回合里等你处理的一张卡。批准时把 `digest` 原样签进去：服务端核对卡片内容没变。
public struct ChatCard: Codable, Identifiable, Sendable, Equatable {
    public let id: String
    public let text: String
    public let risk: String
    public let status: String
    public let digest: String
    public let operationKind: String?
    public let requiredKey: String?
    public let tainted: Bool?
    public let approvalScope: String?
    public let scopeOptions: [String]?
    public let scopeKeys: [String: String]?
    public struct ScopeLimits: Codable, Sendable, Equatable { public let maxActions: Int; public let ttlSeconds: Int }
    public let scopeLimits: ScopeLimits?
    public var canAllowTurn: Bool {
        ["own_data", "draft"].contains(operationKind ?? "") && risk == "normal" && tainted == false
            && scopeOptions?.contains("turn") == true && scopeKeys?["turn"] == "approval"
            && (scopeLimits?.maxActions ?? 0) > 0 && (scopeLimits?.maxActions ?? 0) <= 10
            && (scopeLimits?.ttlSeconds ?? 0) > 0 && (scopeLimits?.ttlSeconds ?? 0) <= 600
    }
    public var categoryLabel: String {
        ApprovalLabels.category(operationKind)
    }
    /// 高风险的卡批准要用需要 Face ID 的批准钥匙；普通卡点一下（日常钥匙）
    public var needsFaceID: Bool { risk != "normal" || (requiredKey != nil && requiredKey != "device") }
    public var isPending: Bool { status == "pending" }
}

/// 一个 App 发起的回合。`state`：running / waiting（有卡等你）/ done / failed / busy。
public struct ChatTurn: Codable, Sendable, Equatable {
    public let rid: String
    public let requestId: String
    public let state: String
    public let reply: String
    public let error: String
    public let cards: [ChatCard]
    public let outcome: String?
    /// 回合进行中到目前为止的回复（旧服务没有这个字段）。完整回复在回合结束后从主要聊天读。
    public var partial: String? = nil
    /// 执行步骤，每步一句（和 Desktop 时间线同一个摘要），不含参数和结果。
    public var steps: [ChatStep]? = nil
    public var isFinished: Bool { outcome == "unknown" || ["done", "failed", "busy"].contains(state) }
    public var pendingCards: [ChatCard] { isFinished ? [] : cards.filter(\.isPending) }
    /// 正常结束：只有这种情况下排队的消息才自动发出
    public var finishedCleanly: Bool { state == "done" && outcome != "unknown" }
}

/// 回合里的一步。`status`：running / succeeded / failed / blocked / timed_out / cancelled / unknown。
public struct ChatStep: Codable, Identifiable, Sendable, Equatable {
    public let id: String
    public let summary: String
    public let status: String
    public init(id: String, summary: String, status: String) { self.id = id; self.summary = summary; self.status = status }
    public var isRunning: Bool { status == "running" }
    public var statusLabel: String {
        ["running": VCCopy.Status.running, "succeeded": "完成", "failed": VCCopy.Status.failed, "blocked": "被拦下",
         "timed_out": "超时", "cancelled": "已取消"][status] ?? VCCopy.Status.unknown
    }
}

/// 写入的失败要和读取分开：一次签名失败不该把人登出。
public enum OwnerWriteError: Error, LocalizedError, Equatable {
    case writesDisabled, signatureRejected, rejected(String), requestNeedsReview(String), unavailable
    public var errorDescription: String? {
        switch self {
        case .writesDisabled: return "这台助理没有开启手机写入。"
        case .signatureRejected: return "这次请求没有通过设备签名核对，请重试；仍失败就重新登录。"
        case .rejected(let detail): return detail
        case .requestNeedsReview(let detail): return detail
        case .unavailable: return "暂时无法连接助手，这次操作没有提交。"
        }
    }
}

/// Desktop 与 App 共用的动态投影。种类和动作使用开放字符串，未来新增条目仍可显示。
public struct ActivityTimeline: Codable, Sendable, Equatable {
    public let items: [ActivityTimelineItem]
    public let unavailable: [String]
    public let generatedAt: String
    public var unavailableLabels: [String] {
        let labels = ["notices": "通知", "changes": "改动记录", "dispatches": "任务单",
                      "codex": "Codex 动作", "codex_progress": "Codex 进展", "reviews": "自动审核", "paused": "暂停目标"]
        return unavailable.map { labels[$0] ?? "其他来源" }
    }
}

public struct ActivityTimelineItem: Codable, Identifiable, Sendable, Equatable {
    public struct Action: Codable, Sendable, Equatable {
        public let type: String
        public let id: String
    }
    public let id: String
    public let at: String
    public let kind: String
    public let title: String
    public let detail: String
    public let status: String
    public let tainted: Bool
    public let actions: [Action]
    public var kindLabel: String {
        ["notice": "通知", "change": "替你做的", "dispatch": "任务单", "codex": "Codex",
         "review": "自动审核", "paused": "已暂停"][kind] ?? "动态"
    }
    public var date: Date? { ISO8601DateFormatter.activityDate(at) }
}

private extension ISO8601DateFormatter {
    static func activityDate(_ value: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: value) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: value)
    }
}
