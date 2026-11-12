import Foundation

public struct StaffActionIdentity: Equatable, Sendable {
    public let taskId: String
    public let actionId: String
    public let digest: String
    public let nonce: String
    public let round: Int
    public let instance: String
    public let runtime: String
}

/// A read projection of an existing staff wait. Its body and identity cannot grant a role.
public struct StaffActionCard: Codable, Identifiable, Equatable, Sendable {
    public let id: String
    public let taskId: String
    public let digest: String
    public let nonce: String
    public let round: Int
    public let instance: String
    public let runtime: String
    public let memberName: String
    public let roleTitle: String
    public let tool: String
    public let kind: String
    public let message: String
    public let argsText: String
    public let tainted: Bool
    public let risk: String
    public let approvalLevel: String
    public let expiresAt: Double
    public let status: String
    public let canDecide: Bool

    public var identity: StaffActionIdentity {
        .init(taskId: taskId, actionId: id, digest: digest, nonce: nonce, round: round, instance: instance, runtime: runtime)
    }
    public var isPending: Bool { status == "pending" }
    public var requiresDesktop: Bool {
        risk != "normal" || approvalLevel != "normal" || !["read", "own_data", "draft"].contains(kind)
    }
    public func canDecideOnPhone(at now: TimeInterval = Date().timeIntervalSince1970) -> Bool {
        canDecide && isPending && !requiresDesktop && expiresAt > now
            && !id.isEmpty && !taskId.isEmpty && !digest.isEmpty && !nonce.isEmpty
            && round > 0 && !instance.isEmpty && !runtime.isEmpty
    }
}

public struct StaffActionsPage: Decodable, Sendable {
    public let cards: [StaffActionCard]
    public let next: String?
    public let sourceComplete: Bool
}

public struct StaffActionReceipt: Decodable, Sendable {
    public let taskId: String
    public let actionId: String
    public let digest: String
    public let nonce: String
    public let round: Int
    public let instance: String
    public let runtime: String
    public let reason: String
    public let status: String
    public let replayed: Bool
    public var identity: StaffActionIdentity {
        .init(taskId: taskId, actionId: actionId, digest: digest, nonce: nonce, round: round, instance: instance, runtime: runtime)
    }
}

/// Receipt handling only; no automatic POST retry or task-scoped grant.
public struct StaffApprovalState: Sendable {
    public enum Phase: Sendable { case signing, submitted, needsCheck, checking }
    public struct Attempt: Equatable, Sendable {
        public let id: UUID
        public let identity: StaffActionIdentity
        public let approve: Bool
        public let reason: String
    }
    public private(set) var active: Attempt?
    public private(set) var phase: Phase?
    public private(set) var receiptConfirmed = false
    public init() {}
    public var blocksDecisions: Bool { active != nil }
    public var canCheck: Bool { phase == .needsCheck }
    public var message: String {
        switch phase {
        case .signing: return "正在核对这次专员决定…"
        case .submitted: return "决定正在提交，请等待回执…"
        case .checking: return "正在核对专员审批状态…"
        case .needsCheck:
            return receiptConfirmed ? "已收到决定回执，执行结果仍待核对；不会重发。" : "专员审批结果尚未确认，请先核对状态；不会自动重发。"
        case nil: return ""
        }
    }
    public mutating func reset() { active = nil; phase = nil; receiptConfirmed = false }
    public func isCurrent(_ attempt: Attempt) -> Bool { active == attempt }
    public mutating func begin(card: StaffActionCard, approve: Bool, reason: String = "") -> Attempt? {
        let reason = reason.trimmingCharacters(in: .whitespacesAndNewlines)
        guard active == nil, card.canDecideOnPhone(), reason.unicodeScalars.count <= 2000,
              approve || !reason.isEmpty else { return nil }
        let attempt = Attempt(id: UUID(), identity: card.identity, approve: approve, reason: reason)
        active = attempt; phase = .signing; receiptConfirmed = false
        return attempt
    }
    public mutating func willSend(_ attempt: Attempt) -> Bool {
        guard isCurrent(attempt), phase == .signing else { return false }
        phase = .submitted; return true
    }
    @discardableResult public mutating func failed(_ attempt: Attempt) -> Bool {
        guard isCurrent(attempt) else { return false }
        if phase == .signing { reset() } else { phase = .needsCheck }
        return true
    }
    @discardableResult public mutating func receive(_ receipt: StaffActionReceipt, for attempt: Attempt) -> Bool {
        guard isCurrent(attempt), phase == .submitted else { return false }
        receiptConfirmed = receipt.identity == attempt.identity && receipt.reason == attempt.reason
            && receipt.status == (attempt.approve ? "approved" : "denied")
        phase = .needsCheck
        return receiptConfirmed
    }
    public mutating func beginCheck() -> Attempt? {
        guard canCheck, let active else { return nil }
        phase = .checking; return active
    }
    @discardableResult public mutating func checked(_ card: StaffActionCard, for attempt: Attempt) -> Bool {
        guard isCurrent(attempt), phase == .checking, card.taskId == attempt.identity.taskId else { return false }
        if card.identity != attempt.identity || !card.isPending { reset() }
        else if receiptConfirmed { phase = .needsCheck }
        else { reset() } // Explicit fresh read permits a new owner choice, never a retry by itself.
        return true
    }
}
