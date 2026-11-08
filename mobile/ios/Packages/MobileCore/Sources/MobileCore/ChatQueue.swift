import Foundation

/// 回合进行中（或有卡等你）时，新消息先排在这台手机上，这一轮结束后再发（ADR-017 M2）。
///
/// 只有上一轮**正常结束**才自动发下一条；失败、结果无法核对、电脑上正有别的回合、旧请求要核对时
/// 都停住，等主人自己点「发出」——排队时写下的话可能已经不适合上一轮的结果。
/// 队列只在这台手机的内存里，换登录或关掉 App 就没了；每条带自己的请求 id，重试不会起两个回合。
public struct ChatQueue: Equatable, Sendable {
    public struct Item: Identifiable, Equatable, Sendable {
        /// 发出时用的请求 id（重试复用）
        public let id: String
        public let text: String
    }

    public enum Next: Equatable, Sendable {
        case none
        case send(Item)
        case hold(String)
    }

    public static let limit = 5
    public private(set) var items: [Item] = []
    /// 为什么停住不自动发；有值时只能手动「发出」
    public private(set) var held: String?

    public init() {}

    public var isFull: Bool { items.count >= Self.limit }

    /// 排一条；空消息或排满返回 false。
    @discardableResult
    public mutating func enqueue(_ text: String, requestId: String) -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isFull, !items.contains(where: { $0.id == requestId }) else { return false }
        items.append(Item(id: requestId, text: trimmed))
        return true
    }

    public mutating func remove(_ id: String) {
        items.removeAll { $0.id == id }
        if items.isEmpty { held = nil }
    }

    public mutating func hold(_ reason: String) { if !items.isEmpty { held = reason } }

    /// 第一条已经发出去（服务端受理）
    public mutating func sent(_ id: String) {
        items.removeAll { $0.id == id }
        held = nil
    }

    /// 主人点「发出」：解除停住，返回第一条。
    public mutating func release() -> Item? {
        held = nil
        return items.first
    }

    /// 回合有了新状态（或刚核对完旧请求）时决定下一步。
    public mutating func next(after turn: ChatTurn?, needsReview: Bool) -> Next {
        guard let first = items.first, held == nil else { return .none }
        if needsReview { return park("旧请求要先核对，排队的消息不会自动发出；核对后点「发出」。") }
        guard let turn else { return .send(first) }
        guard turn.isFinished else { return .none }
        if turn.finishedCleanly { return .send(first) }
        if turn.state == "busy" { return park("电脑上正在进行另一轮对话，排队的消息先不发；稍后点「发出」。") }
        return park("上一轮没有正常结束，排队的消息先不发；看过结果再点「发出」。")
    }

    private mutating func park(_ reason: String) -> Next {
        held = reason
        return .hold(reason)
    }
}
