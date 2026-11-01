import Foundation

/// 「家里的助手现在连得上吗」：只在打开 App 时核对，不做后台唤醒（iOS 不保证定时唤醒，
/// 靠后台心跳判断会在你没打开 App 时误报，见 ADR-011 / 对抗审查 R4 的取舍）。
public enum ContactStatus: Equatable, Sendable {
    case connected                       // 这次刷新刚连上
    case checking(since: Date?)          // 正在核对；上次连上的时间（若有）
    case unreachable(since: Date?)       // 这次没连上；上次连上的时间（若有）

    public static func evaluate(lastContact: Date?, lastAttemptFailed: Bool, checking: Bool,
                                now: Date = Date()) -> ContactStatus {
        if lastAttemptFailed { return .unreachable(since: lastContact) }
        if checking { return .checking(since: lastContact) }
        if let lastContact, now.timeIntervalSince(lastContact) < 120 { return .connected }
        return .checking(since: lastContact)
    }

    public func text(now: Date = Date()) -> String {
        switch self {
        case .connected: return "刚刚连上家里的助手"
        case .checking(let since):
            return since.map { "上次连上：\(Self.ago($0, now: now))，正在重新核对…" } ?? "正在连接家里的助手…"
        case .unreachable(let since):
            let base = since.map { "连不上家里的助手（上次连上：\(Self.ago($0, now: now))）" } ?? "还没连上过家里的助手"
            return base + "。持续连不上时，可能是常驻电脑断电、断网或服务停了。"
        }
    }

    public var isProblem: Bool { if case .unreachable = self { return true }; return false }

    static func ago(_ date: Date, now: Date) -> String {
        let seconds = max(0, Int(now.timeIntervalSince(date)))
        if seconds < 60 { return "不到 1 分钟前" }
        if seconds < 3600 { return "\(seconds / 60) 分钟前" }
        if seconds < 86400 { return "\(seconds / 3600) 小时前" }
        return "\(seconds / 86400) 天前"
    }
}
