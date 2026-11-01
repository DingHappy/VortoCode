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

public struct CaptureOutcome: Codable, Sendable {
    public let goalId: String
    public let objective: String
}

/// 写入的失败要和读取分开：一次签名失败不该把人登出。
public enum OwnerWriteError: Error, LocalizedError, Equatable {
    case writesDisabled, signatureRejected, rejected(String), unavailable
    public var errorDescription: String? {
        switch self {
        case .writesDisabled: return "这台助理没有开启手机写入。"
        case .signatureRejected: return "这次请求没有通过设备签名核对，请重试；仍失败就重新登录。"
        case .rejected(let detail): return detail
        case .unavailable: return "暂时无法连接助手，这次操作没有提交。"
        }
    }
}
