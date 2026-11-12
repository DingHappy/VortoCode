import Foundation

/// 主要聊天顶部、头像下面那一句：后台此刻在做什么（ADR-017 M1，对应 Desktop 对话标题栏）。
/// 只用已经读到的数据，读不到就照实说，不把「没读到」说成「没事」。
public struct AssistantStatusLine: Equatable, Sendable {
    public enum Tone: Sendable, Equatable { case waiting, running, problem, quiet }
    public let text: String
    public let tone: Tone

    public init(text: String, tone: Tone) { self.text = text; self.tone = tone }

    /// - Parameters:
    ///   - waiting: 等你处理的件数（对话里的确认卡、Codex 动作、任务单）
    ///   - waitingUncertain: 有来源没读到，件数可能不全
    ///   - activeGoals: 跟进中的目标数；`nil` 表示目标还没读到
    public static func make(contactProblem: Bool, canWrite: Bool, turnRunning: Bool, waiting: Int,
                            waitingUncertain: Bool = false, activeGoals: Int?) -> AssistantStatusLine {
        if contactProblem { return .init(text: "连不上常驻电脑", tone: .problem) }
        if waiting > 0 {
            return .init(text: "\(waiting)\(waitingUncertain ? "+" : "") 件\(VCCopy.Nav.waiting)", tone: .waiting)
        }
        if turnRunning { return .init(text: "\(VCCopy.Status.running)：处理你刚说的事", tone: .running) }
        if !canWrite { return .init(text: "手机还不能发消息，只能查看", tone: .quiet) }
        if waitingUncertain { return .init(text: "部分来源还没读到", tone: .quiet) }
        guard let activeGoals else { return .init(text: "目标还没读到", tone: .quiet) }
        if activeGoals > 0 { return .init(text: "在跟进 \(activeGoals) 个目标", tone: .quiet) }
        return .init(text: "现在没有在跟进的目标", tone: .quiet)
    }
}

/// 「等你处理」里的一条（对应 Desktop 的 `WaitingInbox`）：只是入口，决定在原来的卡片上做。
public struct WaitingItem: Identifiable, Equatable, Sendable {
    public enum Kind: Sendable, Equatable {
        /// 对话里的确认卡：挡着新消息，排最前
        case card
        /// 专员的单次动作；高级别卡片只能在电脑决定
        case staff
        /// 等你批准的 Codex 动作
        case decision
        /// 等你下发的任务单
        case dispatch
        /// 任务小组：交付了等你验收，或在等你回答
        case team
    }
    public let id: String
    public let kind: Kind
    public let title: String
    public let detail: String
    public let tainted: Bool

    public static func collect(cards: [ChatCard], decisions: [PendingDecision],
                               dispatches: [DispatchProposal], teams: [TeamSummary] = [], staff: [StaffActionCard] = []) -> [WaitingItem] {
        let fromCards: [WaitingItem] = cards.map { card in
            WaitingItem(id: "card:" + card.id, kind: .card, title: "需要你确认 · " + card.categoryLabel,
                        detail: firstLine(card.text), tainted: card.tainted == true)
        }
        let fromDecisions: [WaitingItem] = decisions.map { decision in
            let detail = decision.text.isEmpty ? "叫停正在跑的这一轮" : firstLine(decision.text)
            return WaitingItem(id: "decision:" + decision.id, kind: .decision, title: decision.title + " · " + decision.thread,
                               detail: detail, tainted: decision.tainted)
        }
        let fromDispatches: [WaitingItem] = dispatches.map { proposal in
            WaitingItem(id: "dispatch:" + proposal.id, kind: .dispatch, title: "任务单 · " + (proposal.project ?? "项目"),
                        detail: proposal.title, tainted: proposal.tainted)
        }
        let fromTeams: [WaitingItem] = teams.filter(\.needsOwner).map { team in
            let detail = team.status == "waiting" && !(team.waitingReason ?? "").isEmpty && !team.tainted
                ? team.waitingReason ?? "" : team.objective
            return WaitingItem(id: "team:" + team.id, kind: .team, title: "\(VCCopy.Nav.teams) · " + team.statusLabel,
                               detail: firstLine(detail), tainted: team.tainted)
        }
        // 和 Desktop 一样：小组排在确认卡之后、Codex 动作和任务单之前
        let fromStaff = staff.filter(\.isPending).map { card in
            WaitingItem(id: "staff:" + card.id, kind: .staff,
                title: card.memberName + (card.canDecideOnPhone() ? " · 需要你确认" : " · 需要在电脑上批准"),
                detail: firstLine(card.message), tainted: card.tainted)
        }
        return fromCards + fromStaff + fromTeams + fromDecisions + fromDispatches
    }

    static func firstLine(_ text: String) -> String {
        let line = text.split(whereSeparator: \.isNewline).first.map(String.init) ?? ""
        return line.count > 80 ? String(line.prefix(80)) + "…" : line
    }
}

/// 助理详情的五段，名字和 Desktop 右侧面板一样（design/copy.json）。
public enum AssistantSegment: String, CaseIterable, Identifiable, Sendable {
    case feed, goals, audit, schedule, identity
    public var id: String { rawValue }
    public var title: String {
        switch self {
        case .feed: return VCCopy.Panel.feed
        case .goals: return VCCopy.Panel.goals
        case .audit: return VCCopy.Panel.audit
        case .schedule: return VCCopy.Panel.schedule
        case .identity: return VCCopy.Panel.identity
        }
    }
    public var hint: String {
        switch self {
        case .feed: return VCCopy.PanelHint.feed
        case .goals: return VCCopy.PanelHint.goals
        case .audit: return VCCopy.PanelHint.audit
        case .schedule: return VCCopy.PanelHint.schedule
        case .identity: return VCCopy.PanelHint.identity
        }
    }
}

/// 头像里的字：名字的第一个字（英文转大写），没有名字用「V」，和 Desktop 的 avatarInitial 一样。
public func assistantInitial(_ name: String?) -> String {
    guard let first = name?.trimmingCharacters(in: .whitespacesAndNewlines).first else { return "V" }
    return String(first).uppercased()
}
