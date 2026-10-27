import Foundation

public enum MobileError: Error, LocalizedError, Equatable {
    case unauthorized, unavailable, invalidResponse, cancelled, secureStorage
    public var errorDescription: String? {
        switch self {
        case .unauthorized: return "登录或设备授权已失效，请重新登录。"
        case .unavailable: return "暂时无法连接助手，请检查网络后刷新。"
        case .invalidResponse: return "无法核验助手的响应，请检查服务版本与连接地址。"
        case .cancelled: return "本次登录已取消。"
        case .secureStorage: return "暂时无法读写本机安全会话，请解锁设备后重试。"
        }
    }
}

public struct NativeCredential: Codable, Sendable, CustomDebugStringConvertible {
    public let schemaVersion: Int
    public let token: String
    public let instanceId: String
    public let name: String
    public let expiresAt: Int
    public var debugDescription: String { "NativeCredential(redacted)" }
    public func validate(now: Date = Date()) throws {
        guard schemaVersion == 1, token.range(of: "^vcn_[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil,
              instanceId.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil,
              name.count <= 80, expiresAt > Int(now.timeIntervalSince1970) else { throw MobileError.unauthorized }
    }
}

public struct Goal: Codable, Identifiable, Sendable {
    public let id: String
    public let objective: String
    public let status: String
    public let nextAction: String
    public let blocker: String
    public let revision: Int
    public var statusLabel: String {
        ["active":"跟进中", "blocked":"有阻塞", "achieved":"已达成", "paused":"已暂停", "cancelled":"已取消"][status] ?? "待核对"
    }
}
public struct Source<Value: Codable & Sendable>: Codable, Sendable {
    public let status: String
    public let checkedAt: String?
    public let data: Value?
    public var checkedDate: Date? { checkedAt.flatMap(Self.date) }
    static func date(_ raw: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: raw) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: raw)
    }
    func validate() throws {
        if status == "unavailable", data == nil, checkedAt == nil { return }
        guard status == "current", data != nil, checkedDate != nil else { throw MobileError.invalidResponse }
    }
}
public struct Profile: Codable, Sendable { public let name: String }
public struct MainChat: Codable, Sendable { public let sid: String? }
public struct Goals: Codable, Sendable { public let items: [Goal]; public let truncated: Bool }
public struct Sources: Codable, Sendable {
    public let profile: Source<Profile>
    public let mainChat: Source<MainChat>
    public let goals: Source<Goals>
}
public struct MobileSnapshot: Codable, Sendable {
    public let schemaVersion: Int
    public let instanceId: String
    public let sources: Sources
    public func validate(instance: String) throws {
        guard schemaVersion == 1, instanceId == instance else { throw MobileError.unauthorized }
        try sources.profile.validate(); try sources.mainChat.validate(); try sources.goals.validate()
        guard sources.profile.data?.name.count ?? 0 <= 80 else { throw MobileError.invalidResponse }
        if let sid = sources.mainChat.data?.sid, sid.range(of: "^[A-Za-z0-9_-]{1,80}$", options: .regularExpression) == nil { throw MobileError.invalidResponse }
        if let goals = sources.goals.data {
            guard goals.items.count <= 100, Set(goals.items.map(\.id)).count == goals.items.count else { throw MobileError.invalidResponse }
            for goal in goals.items {
                guard goal.id.range(of: "^personal-[A-Za-z0-9_-]{1,64}$", options: .regularExpression) != nil,
                      goal.revision > 0, [goal.objective, goal.nextAction, goal.blocker].allSatisfy({ $0.count <= 4096 }) else { throw MobileError.invalidResponse }
            }
        }
    }
}
