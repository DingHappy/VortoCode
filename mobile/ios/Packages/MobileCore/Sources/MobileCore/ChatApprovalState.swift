import Foundation

/// Client receipt state only. Never creates an approval, persists a grant or retries a write.
public struct ChatApprovalState: Sendable {
    public enum Phase: Sendable { case signing, submitted, needsCheck, checking }
    public struct Attempt: Equatable, Sendable {
        public let id: UUID
        public let rid: String
        public let cardId: String
        public let digest: String
        public let approve: Bool
        public let scope: String
    }
    public private(set) var active: Attempt?
    public private(set) var phase: Phase?
    public private(set) var receiptConfirmed = false
    public init() {}

    public var blocksDecisions: Bool { active != nil }
    public var canCheck: Bool { phase == .needsCheck }
    public var message: String {
        switch phase {
        case .signing: return "正在核对这次决定…"
        case .submitted: return "决定正在提交，请等待回执…"
        case .checking: return "正在核对运行端状态…"
        case .needsCheck:
            return receiptConfirmed ? "已收到审批回执，进度仍待核对；不会重新提交这张卡。"
                : "审批结果尚未确认，请先核对状态；不会自动重发。"
        case nil: return ""
        }
    }

    public mutating func reset() {
        active = nil; phase = nil; receiptConfirmed = false
    }

    public mutating func begin(rid: String, card: ChatCard, approve: Bool, scope: String = "once") -> Attempt? {
        guard active == nil, !rid.isEmpty, card.isPending, !card.id.isEmpty, !card.digest.isEmpty,
              scope == "once" || (approve && scope == "turn" && card.canAllowTurn) else { return nil }
        let attempt = Attempt(id: UUID(), rid: rid, cardId: card.id, digest: card.digest, approve: approve, scope: scope)
        active = attempt; phase = .signing; receiptConfirmed = false
        return attempt
    }

    public func isCurrent(_ attempt: Attempt) -> Bool { active == attempt }

    /// Called after signing and identity checks, immediately before starting the POST.
    public mutating func willSend(_ attempt: Attempt) -> Bool {
        guard isCurrent(attempt), phase == .signing else { return false }
        phase = .submitted
        return true
    }

    @discardableResult public mutating func failed(_ attempt: Attempt) -> Bool {
        guard isCurrent(attempt) else { return false }
        if phase == .signing { reset() } else { phase = .needsCheck }
        return true
    }

    /// A decoded response alone is not proof: it must match the exact pending choice.
    @discardableResult public mutating func receive(_ card: ChatCard, for attempt: Attempt) -> Bool {
        guard isCurrent(attempt), phase == .submitted else { return false }
        receiptConfirmed = card.id == attempt.cardId && card.digest == attempt.digest
            && card.status == (attempt.approve ? "approved" : "denied")
            && (card.approvalScope ?? "once") == attempt.scope
        phase = .needsCheck
        return receiptConfirmed
    }

    /// Passive polling can close an old wait, but cannot authorize a retry of a pending card.
    public mutating func observe(_ turn: ChatTurn) {
        guard let active, active.rid == turn.rid else { return }
        if turn.isFinished || !turn.pendingCards.contains(where: { $0.id == active.cardId && $0.digest == active.digest }) {
            reset()
        }
    }

    public mutating func beginCheck() -> Attempt? {
        guard canCheck, let active else { return nil }
        phase = .checking
        return active
    }

    /// Only a fresh explicit GET may reopen an uncertain pending choice. It never submits it.
    @discardableResult public mutating func checked(_ turn: ChatTurn, for attempt: Attempt) -> Bool {
        guard isCurrent(attempt), phase == .checking, turn.rid == attempt.rid else { return false }
        observe(turn)
        if isCurrent(attempt) {
            if receiptConfirmed { phase = .needsCheck } else { reset() }
        }
        return true
    }
}
