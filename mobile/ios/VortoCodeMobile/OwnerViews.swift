import SwiftUI
import MobileCore

// MARK: - 共享样式（ADR-017）：颜色、圆角、间距都取 design/tokens.json 生成的 VCColor / VCRadius / VCSpace。
// 字号跟随 Dynamic Type（docs/MOBILE_APP_DESIGN.md「手机按 iOS 习惯处理的部分」），不照搬 Desktop 的像素值。

/// 界面里用到的颜色，跟随系统浅色 / 深色，取值和 Desktop 的 --vc-* 一样。
enum Palette {
    static let bg = VCColor.bg.color
    static let sidebar = VCColor.sidebar.color
    static let surface = VCColor.surface.color
    static let selected = VCColor.selected.color
    static let fill = VCColor.fill.color
    static let border = VCColor.border.color
    static let divider = VCColor.divider.color
    static let backdrop = VCColor.backdrop.color
    static let text = VCColor.text.color
    static let secondary = VCColor.textSecondary.color
    static let muted = VCColor.muted.color
    static let ink = VCColor.ink.color
    static let onInk = VCColor.onInk.color
    static let accent = VCColor.accent.color
    static let accentBg = VCColor.accentBg.color
    static let waitText = VCColor.waitText.color
    static let waitBg = VCColor.waitBg.color
    static let waitDot = VCColor.waitDot.color
    static let dangerText = VCColor.dangerText.color
    static let dangerBg = VCColor.dangerBg.color
    static let doneText = VCColor.doneText.color
    static let doneBg = VCColor.doneBg.color
    static let unknownText = VCColor.unknownText.color
    static let unknownBg = VCColor.unknownBg.color

    static func tone(_ tone: AssistantStatusLine.Tone) -> Color {
        switch tone {
        case .waiting: return waitText
        case .running: return accent
        case .problem: return dangerText
        case .quiet: return muted
        }
    }
}

extension View {
    /// 卡片：表面色、细边、大圆角，不用阴影（DESIGN_SYSTEM 第 5 节）。
    func vcCard(padding: Double = VCSpace.space4) -> some View {
        self.padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Palette.surface, in: RoundedRectangle(cornerRadius: VCRadius.card, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: VCRadius.card, style: .continuous).strokeBorder(Palette.border))
    }
}

/// 主按钮：墨黑底（Desktop 的 .primary）。命中区至少 44 高。
struct VCPrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.subheadline.weight(.medium))
            .padding(.horizontal, VCSpace.space4).frame(minHeight: 44)
            .foregroundStyle(Palette.onInk)
            .background(Palette.ink.opacity(configuration.isPressed ? 0.8 : 1),
                        in: RoundedRectangle(cornerRadius: VCRadius.control, style: .continuous))
            .opacity(enabled ? 1 : 0.4)
    }
}

/// 次按钮：浅底；`danger` 用于拒绝一类的选择（文字变红，不加红底，避免和失败状态混淆）。
struct VCSecondaryButtonStyle: ButtonStyle {
    var danger = false
    @Environment(\.isEnabled) private var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.subheadline.weight(.medium))
            .padding(.horizontal, VCSpace.space4).frame(minHeight: 44)
            .foregroundStyle(danger ? Palette.dangerText : Palette.text)
            .background(configuration.isPressed ? Palette.selected : Palette.fill,
                        in: RoundedRectangle(cornerRadius: VCRadius.control, style: .continuous))
            .opacity(enabled ? 1 : 0.4)
    }
}

/// 状态胶囊：颜色只表达状态（等你 / 在忙 / 办好了 / 无法确认 / 失败）。
struct StatusPill: View {
    enum Kind { case waiting, running, done, unknown, failed }
    let text: String
    let kind: Kind
    private var colors: (Color, Color) {
        switch kind {
        case .waiting: return (Palette.waitText, Palette.waitBg)
        case .running: return (Palette.accent, Palette.accentBg)
        case .done: return (Palette.doneText, Palette.doneBg)
        case .unknown: return (Palette.unknownText, Palette.unknownBg)
        case .failed: return (Palette.dangerText, Palette.dangerBg)
        }
    }
    var body: some View {
        Text(text).font(.caption.weight(.medium)).foregroundStyle(colors.0)
            .padding(.horizontal, VCSpace.space2).padding(.vertical, 2)
            .background(colors.1, in: Capsule())
    }
}

/// 头像：名字首字 + 墨黑底。Desktop 的头像名片同样以首字为兜底。
struct AssistantAvatar: View {
    let name: String?
    var size: Double = 28
    var body: some View {
        Text(assistantInitial(name)).font(.system(size: size * 0.45, weight: .semibold))
            .foregroundStyle(Palette.onInk)
            .frame(width: size, height: size)
            .background(Palette.ink, in: Circle())
            .accessibilityHidden(true)
    }
}

/// 审批卡的统一外形（对话里的确认卡、Codex 动作、任务单）。字段顺序和 Desktop 相同：
/// 标题与状态 → 类别和级别 → 读过外部内容的提醒 → 正文 → 说明 → 按钮（拒绝在左，同意在右）。
struct ApprovalCardFrame<Extra: View, Actions: View>: View {
    let title: String
    let meta: String
    let tainted: String?
    let text: String
    @ViewBuilder var extra: Extra
    @ViewBuilder var actions: Actions
    var body: some View {
        VStack(alignment: .leading, spacing: VCSpace.space3) {
            HStack(spacing: VCSpace.space2) {
                StatusPill(text: VCCopy.Status.waiting, kind: .waiting)
                Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(Palette.text)
            }
            if !meta.isEmpty { Text(meta).font(.footnote).foregroundStyle(Palette.muted) }
            if let tainted {
                Label(tainted, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(Palette.waitText)
            }
            Text(text).font(.body).foregroundStyle(Palette.text).textSelection(.enabled)
            extra
            HStack(spacing: VCSpace.space3) { actions }
        }.vcCard()
    }
}

/// 动态里的一条（和 Desktop 的动态记录行同字段）：标题 → 种类 · 时间 · 状态 → 提醒 → 详情 → 可做的动作。
struct ActivityRow<Actions: View>: View {
    let item: ActivityTimelineItem
    @ViewBuilder var actions: Actions
    var body: some View {
        VStack(alignment: .leading, spacing: VCSpace.space2) {
            Text(item.title).font(.subheadline.weight(.semibold)).foregroundStyle(Palette.text)
            HStack(spacing: VCSpace.space2) {
                Text(item.kindLabel)
                if let date = item.date { Text(date, format: .dateTime.month().day().hour().minute()) }
                if !item.status.isEmpty { Text(item.status) }
            }.font(.caption).foregroundStyle(Palette.muted)
            if item.tainted { Text("读过外部内容后拟的，请核对").font(.caption).foregroundStyle(Palette.waitText) }
            if !item.detail.isEmpty {
                DisclosureGroup("详情") { Text(item.detail).font(.footnote).textSelection(.enabled) }
                    .font(.footnote).tint(Palette.secondary)
            }
            actions
        }.padding(.vertical, VCSpace.space1)
    }
}

/// 主要聊天顶部的「等你处理」（对应 Desktop 的 WaitingInbox，ADR-016）：只是入口，在原来的卡片上决定。
struct WaitingBand: View {
    let items: [WaitingItem]
    let uncertain: Bool
    @Binding var expanded: Bool
    let open: (WaitingItem) -> Void
    var body: some View {
        if !items.isEmpty || uncertain {
            VStack(alignment: .leading, spacing: VCSpace.space2) {
                if items.isEmpty {
                    Text("部分来源还没读到，等你处理的可能不止这些").font(.footnote).foregroundStyle(Palette.muted)
                        .frame(minHeight: 32)
                } else {
                    Button { withAnimation(.snappy) { expanded.toggle() } } label: {
                        HStack(spacing: VCSpace.space2) {
                            Text(VCCopy.Nav.waiting).font(.subheadline.weight(.medium)).foregroundStyle(Palette.secondary)
                            Text("\(items.count)\(uncertain ? "+" : "")").font(.caption.weight(.medium))
                                .foregroundStyle(Palette.waitText).padding(.horizontal, VCSpace.space2)
                                .background(Palette.waitBg, in: Capsule())
                            Image(systemName: "chevron.down").font(.caption).foregroundStyle(Palette.muted)
                                .rotationEffect(.degrees(expanded ? 180 : 0))
                            Spacer()
                        }.frame(minHeight: 44).contentShape(Rectangle())
                    }.buttonStyle(.plain)
                        .accessibilityLabel("\(VCCopy.Nav.waiting) \(items.count) 件，\(expanded ? "收起" : "展开")")
                        .accessibilityIdentifier("native-waiting-band")
                    if expanded {
                        VStack(spacing: 0) {
                            ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                                if index > 0 { Divider().overlay(Palette.divider) }
                                HStack(alignment: .center, spacing: VCSpace.space3) {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(item.title).font(.subheadline.weight(.medium)).foregroundStyle(Palette.text).lineLimit(2)
                                        if !item.detail.isEmpty { Text(item.detail).font(.caption).foregroundStyle(Palette.muted).lineLimit(2) }
                                        if item.tainted { Text("读过外部内容后拟的").font(.caption).foregroundStyle(Palette.waitText) }
                                    }
                                    Spacer(minLength: 0)
                                    Button("去处理") { open(item) }.buttonStyle(VCSecondaryButtonStyle())
                                }.padding(.horizontal, VCSpace.space4).padding(.vertical, VCSpace.space3)
                            }
                        }
                        .background(Palette.surface, in: RoundedRectangle(cornerRadius: VCRadius.card, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: VCRadius.card, style: .continuous).strokeBorder(Palette.border))
                    }
                }
            }
            .padding(.horizontal, VCSpace.space4).padding(.bottom, VCSpace.space2)
        }
    }
}

// MARK: - 主要聊天

/// 全屏主要聊天（Desktop、Telegram 也在这一段）。回合中途要你确认的事以卡片出现在对话里；
/// 打开「只记下来」时输入框变成随手记。顶部是「等你处理」。
struct ChatView: View {
    let entry: AssistantEntry
    let session: AssistantSession
    @Bindable var owner: OwnerSession
    let waiting: [WaitingItem]
    let waitingUncertain: Bool
    /// Codex 动作和任务单在助理详情的「动态」里处理
    let openActivity: () -> Void
    /// 小组在它自己的群聊里处理
    var openTeam: (String) -> Void = { _ in }
    @Environment(\.scenePhase) private var scenePhase
    @FocusState private var typing: Bool
    @State private var waitingExpanded = false

    var body: some View {
        ScrollViewReader { proxy in
            VStack(spacing: 0) {
                WaitingBand(items: waiting, uncertain: waitingUncertain, expanded: $waitingExpanded) { item in
                    switch item.kind {
                    case .card: withAnimation { proxy.scrollTo(item.id, anchor: .top) }
                    case .team: openTeam(String(item.id.dropFirst("team:".count)))
                    default: openActivity()
                    }
                }
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: VCSpace.space4) {
                        if !owner.canWrite {
                            notice(owner.readOnlyReason)
                        } else if !owner.chatRegistered {
                            notice("主要聊天还没开通：先在电脑上的 VortoOne 里打开一次助理对话。")
                        } else if owner.chat.isEmpty && owner.turn == nil {
                            notice("跟助理说件事吧。比如「帮我把这周要交的东西列一下」。")
                        }
                        ForEach(Array(owner.chat.enumerated()), id: \.offset) { _, item in message(item) }
                        if let turn = owner.turn, !turn.isFinished { progress(turn) }
                        ForEach(Array(owner.queue.items.enumerated()), id: \.element.id) { index, item in
                            queued(item, first: index == 0)
                        }
                        Color.clear.frame(height: 1).id("bottom")
                    }.padding(.horizontal, VCSpace.space4).padding(.top, VCSpace.space2)
                }
                // 点消息区域或往下拖都收起键盘
                .scrollDismissesKeyboard(.interactively)
                .contentShape(Rectangle())
                .onTapGesture { typing = false }
                .onChange(of: owner.chat.count) { _, _ in withAnimation { proxy.scrollTo("bottom") } }
                .onChange(of: owner.turn?.cards.count) { _, _ in withAnimation { proxy.scrollTo("bottom") } }
                .onChange(of: owner.turn?.steps?.count) { _, _ in withAnimation { proxy.scrollTo("bottom") } }
                .onChange(of: owner.queue.items.count) { _, _ in withAnimation { proxy.scrollTo("bottom") } }
                .refreshable { if let credential = session.credential { await owner.loadChat(entry, credential) } }
                if !owner.note.isEmpty {
                    Text(owner.note).font(.footnote).foregroundStyle(Palette.muted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, VCSpace.space4).padding(.top, VCSpace.space2)
                }
                if owner.chatNeedsReview {
                    Button("已核对记录，准备新请求") { owner.prepareReviewedRequest() }
                        .buttonStyle(VCSecondaryButtonStyle()).padding(.horizontal, VCSpace.space4)
                        .accessibilityIdentifier("native-reviewed-new-request")
                }
                composer
            }
        }
        .background(Palette.bg)
        .onChange(of: scenePhase) { _, phase in
            guard let credential = session.credential else { return }
            if phase == .active { owner.follow(entry, credential); Task { await owner.loadChat(entry, credential) } }
            else if phase == .background { owner.stopFollowing() }
        }
    }

    private func notice(_ text: String) -> some View {
        Text(text).font(.subheadline).foregroundStyle(Palette.muted).vcCard(padding: VCSpace.space3)
    }

    /// 自己的话放浅底气泡靠右；助理的回复不加气泡、占满宽度（和 Desktop 一样）。
    @ViewBuilder private func message(_ item: ChatItem) -> some View {
        if item.isMine {
            HStack {
                Spacer(minLength: VCSpace.space10)
                VStack(alignment: .trailing, spacing: VCSpace.space1) {
                    if let label = item.sourceLabel { Text("来自\(label)").font(.caption2).foregroundStyle(Palette.muted) }
                    Text(item.text).font(.body).foregroundStyle(Palette.text).textSelection(.enabled)
                        .padding(.horizontal, VCSpace.space4).padding(.vertical, VCSpace.space2 + 2)
                        .background(Palette.fill, in: RoundedRectangle(cornerRadius: VCRadius.bubble, style: .continuous))
                }
            }
        } else {
            VStack(alignment: .leading, spacing: VCSpace.space1) {
                if let label = item.sourceLabel { Text("来自\(label)").font(.caption2).foregroundStyle(Palette.muted) }
                Text(item.text).font(.body).foregroundStyle(Palette.text).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading).lineSpacing(3)
            }
        }
    }

    /// 进行中的一轮：执行过程 → 到目前为止的回复（逐字长出来）→ 等你处理的卡。
    @ViewBuilder private func progress(_ turn: ChatTurn) -> some View {
        if let steps = turn.steps, !steps.isEmpty { StepsView(steps: steps) }
        if let partial = turn.partial, !partial.isEmpty {
            StreamingText(text: partial)
        } else if turn.pendingCards.isEmpty {
            HStack(spacing: VCSpace.space2) {
                ProgressView().controlSize(.small)
                Text("\(VCCopy.Status.running)：助理正在处理…").foregroundStyle(Palette.muted)
            }.font(.subheadline)
        }
        ForEach(turn.pendingCards) { card in
            ApprovalCardFrame(
                title: "需要你确认",
                meta: [card.categoryLabel, card.needsFaceID ? VCCopy.ApprovalRisk.high : VCCopy.ApprovalRisk.normal,
                       VCCopy.ApprovalScope.once].joined(separator: " · "),
                tainted: card.tainted == true ? "读过外部内容后拟的，请核对是否符合你的本意。" : nil,
                text: card.text
            ) {
                Text("你处理之前，新消息会排队，这一轮结束后再发；10 分钟没处理按不同意。").font(.footnote).foregroundStyle(Palette.muted)
                if owner.chatApproval.active?.cardId == card.id {
                    Text(owner.chatApproval.message).font(.footnote).foregroundStyle(Palette.secondary)
                        .accessibilityIdentifier("native-chat-approval-status")
                    if owner.chatApproval.canCheck || owner.chatApproval.phase == .checking {
                        Button("核对状态") { checkDecision() }.buttonStyle(VCSecondaryButtonStyle())
                            .disabled(owner.working || !owner.chatApproval.canCheck || !owner.canWrite || session.credential == nil)
                            .accessibilityIdentifier("native-chat-approval-check")
                    }
                }
            } actions: {
                VStack(alignment: .leading, spacing: VCSpace.space2) {
                    HStack(spacing: VCSpace.space3) {
                        Button("不同意") { decide(card, approve: false) }.buttonStyle(VCSecondaryButtonStyle(danger: true))
                        Spacer()
                        Button(card.needsFaceID ? "同意（Face ID）" : "同意") { decide(card, approve: true) }
                            .buttonStyle(VCPrimaryButtonStyle())
                    }.disabled(cardLocked)
                    if card.canAllowTurn {
                        Button("本轮同类允许（Face ID，最多 \(card.scopeLimits?.maxActions ?? 0) 次）") { decide(card, approve: true, scope: "turn") }
                            .buttonStyle(VCSecondaryButtonStyle()).disabled(cardLocked)
                        Text("含本次，仅覆盖\(card.categoryLabel)类操作。从本轮开始最多 \(((card.scopeLimits?.ttlSeconds ?? 0) + 59) / 60) 分钟，回合结束或读过外部内容后失效。")
                            .font(.footnote).foregroundStyle(Palette.muted)
                    }
                }
            }.id("card:" + card.id)
        }
    }

    private var cardLocked: Bool {
        owner.working || owner.chatApproval.blocksDecisions || !owner.canWrite || session.credential == nil
    }

    private var waitingForCard: Bool { !(owner.turn?.pendingCards.isEmpty ?? true) }
    /// 现在点发送会排队（这一轮没结束，或前面还有排着的）
    private var willQueue: Bool { !owner.captureOnly && (owner.turnInProgress || !owner.queue.items.isEmpty) }

    /// 排着的消息：浅色虚线气泡；停住时第一条可以手动「发出」。
    private func queued(_ item: ChatQueue.Item, first: Bool) -> some View {
        HStack {
            Spacer(minLength: VCSpace.space10)
            VStack(alignment: .trailing, spacing: VCSpace.space1) {
                Text(item.text).font(.body).foregroundStyle(Palette.secondary)
                    .padding(.horizontal, VCSpace.space4).padding(.vertical, VCSpace.space2 + 2)
                    .overlay(RoundedRectangle(cornerRadius: VCRadius.bubble, style: .continuous)
                        .strokeBorder(Palette.border, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
                HStack(spacing: VCSpace.space3) {
                    Text(first && owner.queue.held != nil ? "没有自动发出" : "排队中，这一轮结束后发出")
                        .font(.caption).foregroundStyle(first && owner.queue.held != nil ? Palette.waitText : Palette.muted)
                    if first && owner.queue.held != nil && !owner.turnInProgress {
                        Button("发出") { sendQueued() }.font(.caption.weight(.medium)).foregroundStyle(Palette.text)
                            .disabled(owner.sending || owner.chatNeedsReview || session.credential == nil)
                            .accessibilityIdentifier("native-queue-send")
                    }
                    Button("删除") { owner.queue.remove(item.id) }.font(.caption).foregroundStyle(Palette.muted)
                        .accessibilityLabel("删除排队的消息")
                }.frame(minHeight: 32)
            }
        }.accessibilityElement(children: .contain)
    }

    /// 胶囊输入框（Muse / Desktop 同款）：左边切换「只记下来」，右边发送。
    private var composer: some View {
        VStack(alignment: .leading, spacing: VCSpace.space1) {
            if willQueue {
                Text(waitingForCard ? "有卡等你处理；现在发的消息会排队，这一轮结束后发出。" : "这一轮还没结束；现在发的消息会排队（最多 \(ChatQueue.limit) 条）。")
                    .font(.caption).foregroundStyle(waitingForCard ? Palette.waitText : Palette.muted)
            }
            if owner.captureOnly { Text("只记下来：记成你的一个目标，不让助理去做").font(.caption).foregroundStyle(Palette.muted) }
            HStack(alignment: .bottom, spacing: VCSpace.space1) {
                Button { owner.captureOnly.toggle() } label: {
                    Image(systemName: owner.captureOnly ? "note.text" : "note.text.badge.plus")
                        .font(.body).frame(width: 36, height: 36)
                        .foregroundStyle(owner.captureOnly ? Palette.onInk : Palette.secondary)
                        .background(owner.captureOnly ? Palette.ink : Color.clear, in: Circle())
                }.frame(width: 44, height: 44)
                    .accessibilityLabel("只记下来，不让助理去做").accessibilityValue(owner.captureOnly ? "已打开" : "已关闭")
                    .accessibilityIdentifier("native-capture-toggle")
                TextField(owner.captureOnly ? "比如：周五前交周报" : "跟助理说…", text: $owner.draft, axis: .vertical)
                    .lineLimit(1...5).font(.body).foregroundStyle(Palette.text)
                    .focused($typing)
                    .disabled(!owner.canWrite)
                    .padding(.vertical, VCSpace.space3)
                Button { send() } label: {
                    Image(systemName: "arrow.up").font(.body.weight(.semibold))
                        .frame(width: 36, height: 36)
                        .foregroundStyle(Palette.onInk)
                        .background(canSend ? Palette.ink : Palette.muted.opacity(0.4), in: Circle())
                }.frame(width: 44, height: 44)
                    .disabled(!canSend).accessibilityLabel(owner.captureOnly ? "记下" : willQueue ? "排队" : "发送")
                    .accessibilityIdentifier("native-send")
            }
            .padding(.horizontal, VCSpace.space1)
            .background(Palette.surface, in: RoundedRectangle(cornerRadius: VCRadius.input, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: VCRadius.input, style: .continuous).strokeBorder(Palette.border))
        }
        .padding(.horizontal, VCSpace.space3).padding(.vertical, VCSpace.space2)
        .background(Palette.bg)
    }

    private var canSend: Bool {
        guard owner.canWrite, !owner.sending,
              !owner.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return false }
        if owner.captureOnly { return !owner.working }
        if willQueue { return !owner.queue.isFull }        // 排队不发请求，有卡等你时也能写
        return !owner.working && !owner.chatNeedsReview
    }

    private func send() {
        guard let credential = session.credential else { return }
        typing = false
        Task { await owner.send(entry, credential) }
    }

    private func sendQueued() {
        guard let credential = session.credential else { return }
        Task { await owner.sendQueuedNow(entry, credential) }
    }

    private func decide(_ card: ChatCard, approve: Bool, scope: String = "once") {
        guard let credential = session.credential else { return }
        Task {
            await owner.decideCard(entry, credential, card, approve: approve, scope: scope,
                sessionCurrent: { session.credential?.token == credential.token && session.credential?.instanceId == credential.instanceId })
        }
    }

    private func checkDecision() {
        guard let credential = session.credential else { return }
        Task {
            await owner.checkCardDecision(entry, credential,
                sessionCurrent: { session.credential?.token == credential.token && session.credential?.instanceId == credential.instanceId })
        }
    }
}

/// 回合中途的回复：轮询每秒拿到一次「到目前为止的全文」，两次之间把新长出来的字逐步显示。
/// 打开「减弱动态效果」时直接显示全文。
struct StreamingText: View {
    let text: String
    @State private var shown = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var body: some View {
        Text(String(text.prefix(shown))).font(.body).foregroundStyle(Palette.text).textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading).lineSpacing(3)
            .accessibilityLabel(text)
            .task(id: text) {
                let target = text.count
                if reduceMotion || shown > target { shown = target; return }
                let frames = 30
                let stride = max(1, (target - shown + frames - 1) / frames)
                while shown < target && !Task.isCancelled {
                    shown = min(target, shown + stride)
                    try? await Task.sleep(for: .milliseconds(25))
                }
            }
            .accessibilityIdentifier("native-streaming-reply")
    }
}

/// 执行过程（和 Desktop 同名的折叠块）：默认收起，只显示正在做的那一步；展开看每一步和状态。
struct StepsView: View {
    let steps: [ChatStep]
    @State private var expanded = false
    var body: some View {
        VStack(alignment: .leading, spacing: VCSpace.space1) {
            Button { withAnimation(.snappy) { expanded.toggle() } } label: {
                HStack(spacing: VCSpace.space2) {
                    Image(systemName: "chevron.right").font(.caption2).rotationEffect(.degrees(expanded ? 90 : 0))
                    Text("执行过程 · \(steps.count) 步").fixedSize()
                    if !expanded, let current = steps.last(where: \.isRunning) ?? steps.last {
                        Text(current.summary).lineLimit(1).truncationMode(.tail)
                    }
                    Spacer(minLength: 0)
                }
                .font(.footnote).foregroundStyle(Palette.muted).frame(minHeight: 32).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("执行过程，\(steps.count) 步，\(expanded ? "收起" : "展开")")
            .accessibilityIdentifier("native-turn-steps")
            if expanded {
                ForEach(steps) { step in
                    HStack(alignment: .firstTextBaseline, spacing: VCSpace.space2) {
                        icon(step).frame(width: 16)
                        Text(step.summary).foregroundStyle(Palette.secondary).fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: VCSpace.space2)
                        Text(step.statusLabel).foregroundStyle(Palette.muted)
                    }
                    .font(.footnote)
                    .accessibilityElement(children: .combine)
                }
                .padding(.leading, VCSpace.space4)
            }
        }
    }

    @ViewBuilder private func icon(_ step: ChatStep) -> some View {
        switch step.status {
        case "running": ProgressView().controlSize(.mini)
        case "succeeded": Image(systemName: "checkmark").foregroundStyle(Palette.doneText)
        case "failed", "blocked", "timed_out": Image(systemName: "xmark").foregroundStyle(Palette.dangerText)
        default: Image(systemName: "circle").foregroundStyle(Palette.muted)
        }
    }
}

/// 在已经读到的主要聊天里找字（不发请求、不保存查询词）。
struct ChatSearchView: View {
    let owner: OwnerSession
    @State private var query = ""
    @Environment(\.dismiss) private var dismiss
    private var matches: [ChatItem] {
        let terms = query.split(whereSeparator: \.isWhitespace).map(String.init)
        guard !terms.isEmpty else { return [] }
        return owner.chat.filter { item in terms.allSatisfy { item.text.localizedStandardContains($0) } }
    }
    var body: some View {
        List {
            Section {
                Text("只在这台手机已经读到的主要聊天里找，共 \(owner.chat.count) 条；更早的记录请在电脑上找。")
                    .font(.footnote).foregroundStyle(Palette.muted)
            }
            ForEach(Array(matches.enumerated()), id: \.offset) { _, item in
                VStack(alignment: .leading, spacing: VCSpace.space1) {
                    Text(item.isMine ? "你" + (item.sourceLabel.map { "（\($0)）" } ?? "") : "助理")
                        .font(.caption).foregroundStyle(Palette.muted)
                    Text(item.text).font(.subheadline).foregroundStyle(Palette.text).lineLimit(6).textSelection(.enabled)
                }
            }
            if !query.isEmpty && matches.isEmpty { Text("没有找到。").foregroundStyle(Palette.muted) }
        }
        .scrollContentBackground(.hidden).background(Palette.bg)
        .navigationTitle(VCCopy.Nav.search).navigationBarTitleDisplayMode(.inline)
        .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: "搜索主要聊天")
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("完成") { dismiss() } } }
    }
}

// MARK: - 助理详情「动态」

/// 动态：等你决定的 Codex 动作和任务单、共用时间线。签名操作沿用原有接口。目标在「目标」段。
struct ActivityView: View {
    let entry: AssistantEntry
    let session: AssistantSession
    let owner: OwnerSession

    var body: some View {
        List {
            Section {
                // 打开 App 时核对一次家里的助手连不连得上；不做后台心跳，所以不会在你没打开时误报
                Label(session.contact.text(), systemImage: session.contact.isProblem ? "wifi.exclamationmark" : "house")
                    .font(.subheadline).foregroundStyle(session.contact.isProblem ? Palette.dangerText : Palette.muted)
                    .accessibilityIdentifier("native-contact")
            }
            if !owner.note.isEmpty { Section { Text(owner.note).font(.subheadline).foregroundStyle(Palette.muted) } }
            if !owner.canWrite {
                Section { Text(owner.readOnlyReason).foregroundStyle(Palette.muted) }
            } else {
                Section(VCCopy.Nav.waiting) {
                    if owner.decisions.isEmpty && owner.dispatches.isEmpty && owner.staffActions.isEmpty
                        && owner.staffActionsComplete && owner.staffActionsError.isEmpty {
                        Text("现在没有等你批准的事。在对话里说「让 Codex 把…」「派个任务去…」，助理拟好会出现在这里。")
                            .foregroundStyle(Palette.muted)
                    }
                    ForEach(owner.dispatches) { proposal in dispatchRow(proposal) }
                    ForEach(owner.decisions) { decision in decisionRow(decision) }
                    if !owner.staffActionsError.isEmpty {
                        Text(owner.staffActionsError).font(.footnote).foregroundStyle(Palette.waitText)
                    } else if !owner.staffActionsComplete {
                        Text("专员审批来源还未完整核对，件数可能不全；不代表没有待批动作。")
                            .font(.footnote).foregroundStyle(Palette.waitText)
                    }
                    if owner.staffApproval.blocksDecisions {
                        Text(owner.staffApproval.message).font(.footnote).foregroundStyle(Palette.waitText)
                        if owner.staffApproval.canCheck {
                            Button("核对专员审批状态") {
                                guard let credential = session.credential else { return }
                                Task { await owner.checkStaffActionDecision(entry, credential,
                                    sessionCurrent: { [session] in session.credential?.token == credential.token && session.credential?.instanceId == credential.instanceId }) }
                            }.buttonStyle(VCSecondaryButtonStyle()).disabled(owner.working)
                        }
                    }
                    ForEach(owner.staffActions) { card in
                        StaffActionRow(card: card, disabled: owner.working || owner.staffApproval.blocksDecisions || !owner.staffActionsError.isEmpty) { approve, reason in
                            guard let credential = session.credential else { return }
                            Task { await owner.decideStaffAction(entry, credential, card, approve: approve, reason: reason,
                                sessionCurrent: { [session] in session.credential?.token == credential.token && session.credential?.instanceId == credential.instanceId }) }
                        }
                    }
                }
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets(top: VCSpace.space2, leading: VCSpace.space4, bottom: VCSpace.space2, trailing: VCSpace.space4))
                timelineSection
            }
        }
        .scrollContentBackground(.hidden).background(Palette.bg)
        .refreshable {
            if let credential = session.credential { await owner.refresh(entry, credential) }
            await session.refreshAndWait(entry)
        }
    }

    private var timelineSection: some View {
        Section("时间线") {
            if !owner.timelineError.isEmpty {
                Text(owner.timelineError).font(.footnote).foregroundStyle(Palette.waitText)
            }
            if let timeline = owner.timeline {
                if !timeline.unavailable.isEmpty {
                    Text("部分来源暂时读不到（" + timeline.unavailableLabels.joined(separator: "、") + "），不代表没有动静。")
                        .font(.footnote).foregroundStyle(Palette.waitText)
                }
                if timeline.items.isEmpty && timeline.unavailable.isEmpty {
                    Text("还没有动静。").foregroundStyle(Palette.muted)
                }
                ForEach(timeline.items) { item in
                    ActivityRow(item: item) {
                        ForEach(Array(item.actions.enumerated()), id: \.offset) { _, action in
                            if action.type == "undo" || action.type == "resume_goal" {
                                Button(action.type == "undo" ? "撤销" : "恢复推进") {
                                    guard let credential = session.credential else { return }
                                    Task { await owner.actOnTimeline(entry, credential, action) }
                                }.buttonStyle(VCSecondaryButtonStyle()).disabled(owner.working)
                            }
                        }
                    }
                }
            } else if owner.timelineError.isEmpty {
                ProgressView("正在读取时间线…")
            }
        }.listRowBackground(Palette.surface)
    }

    private func decisionRow(_ decision: PendingDecision) -> some View {
        ApprovalCardFrame(title: decision.title, meta: decision.thread + " · " + VCCopy.ApprovalRisk.high,
                          tainted: decision.tainted ? "这条是助理读过外部内容后拟的，请看清楚再批" : nil,
                          text: decision.text.isEmpty ? "（叫停正在跑的这一轮）" : decision.text) {
            EmptyView()
        } actions: {
            Button("拒绝") { act(decision, approve: false) }.buttonStyle(VCSecondaryButtonStyle(danger: true))
            Spacer()
            Button("批准（Face ID）") { act(decision, approve: true) }.buttonStyle(VCPrimaryButtonStyle())
        }.disabled(owner.working)
    }

    private func dispatchRow(_ proposal: DispatchProposal) -> some View {
        ApprovalCardFrame(title: "任务单 · \(proposal.project ?? "项目")", meta: proposal.title,
                          tainted: proposal.tainted ? "助理读过外部内容后拟的，请到电脑上核对后下发" : nil,
                          text: proposal.goal) {
            if !proposal.criteria.isEmpty {
                Text("验收：" + proposal.criteria.joined(separator: "；")).font(.footnote).foregroundStyle(Palette.muted)
            }
        } actions: {
            Button("撤回") { dispatch(proposal, approve: false) }.buttonStyle(VCSecondaryButtonStyle(danger: true))
            Spacer()
            if !proposal.tainted {
                Button("下发（Face ID）") { dispatch(proposal, approve: true) }.buttonStyle(VCPrimaryButtonStyle())
            }
        }.disabled(owner.working)
    }

    private func dispatch(_ proposal: DispatchProposal, approve: Bool) {
        guard let credential = session.credential else { return }
        Task { await owner.decideDispatch(entry, credential, proposal, approve: approve) }
    }

    private func act(_ decision: PendingDecision, approve: Bool) {
        guard let credential = session.credential else { return }
        Task { await owner.decide(entry, credential, decision, approve: approve) }
    }
}

/// Only the single displayed action is decided; higher categories have no mobile buttons.
private struct StaffActionRow: View {
    let card: StaffActionCard
    let disabled: Bool
    let decide: (Bool, String) -> Void
    @State private var reason = ""
    var body: some View {
        ApprovalCardFrame(title: card.memberName + " · " + ApprovalLabels.category(card.kind),
            meta: card.roleTitle + " · 第 \(card.round) 轮",
            tainted: card.tainted ? "本回合读过外部内容，请核对这次具体动作；这次批准不产生同类放行。" : nil,
            text: card.message) {
                DisclosureGroup("完整参数与身份") {
                    Text(card.argsText).font(.footnote.monospaced()).textSelection(.enabled)
                    Text("任务：\(card.taskId)\n工具：\(card.tool)").font(.caption).foregroundStyle(Palette.muted)
                }
                if card.canDecideOnPhone() {
                    TextField("不执行的理由（最多 2000 字）", text: $reason, axis: .vertical).font(.subheadline).disabled(disabled)
                } else {
                    Text(card.requiresDesktop && card.isPending ? "需要在电脑上批准，手机仅可查看。" : "这张卡已处理、中断、过期或身份待核对，请先刷新。")
                        .font(.footnote).foregroundStyle(Palette.muted)
                }
            } actions: {
                if card.canDecideOnPhone() {
                    Button("不执行") { decide(false, reason) }.buttonStyle(VCSecondaryButtonStyle(danger: true))
                        .disabled(disabled || reason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            || reason.trimmingCharacters(in: .whitespacesAndNewlines).unicodeScalars.count > 2000)
                    Spacer()
                    Button("批准这一次") { decide(true, "") }.buttonStyle(VCPrimaryButtonStyle()).disabled(disabled)
                }
            }
            .accessibilityIdentifier("native-staff-action-" + card.id)
    }
}
