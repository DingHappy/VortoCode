import SwiftUI
import MobileCore

/// 对话：和常驻助理的主要聊天（Desktop、Telegram 也在这一段）。回合中途要你确认的事以卡片出现在对话里；
/// 打开「只记下来」时输入框变成随手记。
struct ChatView: View {
    let entry: AssistantEntry
    let session: AssistantSession
    @Bindable var owner: OwnerSession
    @Environment(\.scenePhase) private var scenePhase
    @FocusState private var typing: Bool

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 12) {
                        if !owner.canWrite {
                            notice(owner.readOnlyReason)
                        } else if !owner.chatRegistered {
                            notice("主要聊天还没开通：先在电脑上的 VortoCode 里打开一次助理对话。")
                        } else if owner.chat.isEmpty && owner.turn == nil {
                            notice("跟助理说件事吧。比如「帮我把这周要交的东西列一下」。")
                        }
                        ForEach(Array(owner.chat.enumerated()), id: \.offset) { _, item in bubble(item) }
                        if let turn = owner.turn, !turn.isFinished { progress(turn) }
                        Color.clear.frame(height: 1).id("bottom")
                    }.padding(.horizontal).padding(.top, 8)
                }
                // 点消息区域或往下拖都收起键盘（原来点别处键盘退不下）
                .scrollDismissesKeyboard(.interactively)
                .contentShape(Rectangle())
                .onTapGesture { typing = false }
                .onChange(of: owner.chat.count) { _, _ in withAnimation { proxy.scrollTo("bottom") } }
                .onChange(of: owner.turn?.cards.count) { _, _ in withAnimation { proxy.scrollTo("bottom") } }
                .refreshable { if let credential = session.credential { await owner.loadChat(entry, credential) } }
            }
            if !owner.note.isEmpty {
                Text(owner.note).font(.footnote).foregroundStyle(.secondary).padding(.horizontal).padding(.top, 6)
            }
            composer
        }
        .navigationTitle("对话")
        .onChange(of: scenePhase) { _, phase in
            guard let credential = session.credential else { return }
            if phase == .active { owner.follow(entry, credential); Task { await owner.loadChat(entry, credential) } }
            else if phase == .background { owner.stopFollowing() }
        }
    }

    private func notice(_ text: String) -> some View {
        Text(text).font(.subheadline).foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading).padding(12)
            .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 12))
    }

    private func bubble(_ item: ChatItem) -> some View {
        HStack {
            if item.isMine { Spacer(minLength: 40) }
            VStack(alignment: item.isMine ? .trailing : .leading, spacing: 4) {
                if let label = item.sourceLabel {
                    Text("来自\(label)").font(.caption2).foregroundStyle(.secondary)
                }
                Text(item.text).textSelection(.enabled)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(item.isMine ? Color.accentColor.opacity(0.18) : Color(uiColor: .secondarySystemGroupedBackground),
                                in: RoundedRectangle(cornerRadius: 14))
            }
            if !item.isMine { Spacer(minLength: 40) }
        }
    }

    @ViewBuilder private func progress(_ turn: ChatTurn) -> some View {
        if turn.pendingCards.isEmpty {
            HStack(spacing: 8) { ProgressView(); Text("助理正在处理…").foregroundStyle(.secondary) }.font(.subheadline)
        }
        ForEach(turn.pendingCards) { card in
            VStack(alignment: .leading, spacing: 10) {
                Label("需要你确认", systemImage: card.needsFaceID ? "faceid" : "checkmark.circle").font(.headline)
                Text(card.text).textSelection(.enabled)
                Text("你处理之前，新的消息先不发出；10 分钟没处理按不同意。")
                    .font(.footnote).foregroundStyle(.secondary)
                HStack {
                    Button("不同意", role: .destructive) { decide(card, approve: false) }.buttonStyle(.bordered)
                    Spacer()
                    Button(card.needsFaceID ? "同意（Face ID）" : "同意") { decide(card, approve: true) }
                        .buttonStyle(.borderedProminent)
                }.disabled(owner.working)
            }
            .padding(14)
            .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 14))
        }
    }

    private var waitingForCard: Bool { !(owner.turn?.pendingCards.isEmpty ?? true) }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 6) {
            Toggle("只记下来，不让助理去做", isOn: $owner.captureOnly).font(.footnote).tint(.accentColor)
            HStack(alignment: .bottom, spacing: 8) {
                TextField(owner.captureOnly ? "比如：周五前交周报" : "跟助理说…", text: $owner.draft, axis: .vertical)
                    .lineLimit(1...5).textFieldStyle(.roundedBorder)
                    .focused($typing)
                    .disabled(!owner.canWrite)
                Button { send() } label: { Image(systemName: "arrow.up.circle.fill").font(.title2) }
                    .disabled(!canSend).accessibilityLabel(owner.captureOnly ? "记下" : "发送")
            }
            if waitingForCard { Text("先处理上面那张卡，再发新消息。").font(.caption).foregroundStyle(.orange) }
        }
        .padding(.horizontal).padding(.vertical, 8)
        .background(.bar)
    }

    private var canSend: Bool {
        owner.canWrite && !owner.sending && !owner.working && !waitingForCard
            && (owner.captureOnly || (owner.turn?.isFinished ?? true))
            && !owner.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func send() {
        guard let credential = session.credential else { return }
        typing = false
        Task { await owner.send(entry, credential) }
    }

    private func decide(_ card: ChatCard, approve: Bool) {
        guard let credential = session.credential else { return }
        Task { await owner.decideCard(entry, credential, card, approve: approve) }
    }
}

/// 动态：家里的助手连得上吗、等你决定的 Codex 动作、目标、我替你做了什么（可撤销）。
struct ActivityView: View {
    let entry: AssistantEntry
    let session: AssistantSession
    let owner: OwnerSession

    var body: some View {
        List {
            Section {
                // 打开 App 时核对一次家里的助手连不连得上；不做后台心跳，所以不会在你没打开时误报
                Label(session.contact.text(), systemImage: session.contact.isProblem ? "wifi.exclamationmark" : "house")
                    .font(.subheadline).foregroundStyle(session.contact.isProblem ? Color.orange : Color.secondary)
                    .accessibilityIdentifier("native-contact")
            }
            if !owner.note.isEmpty { Section { Text(owner.note).font(.subheadline).foregroundStyle(.secondary) } }
            if !owner.canWrite {
                Section { Text(owner.readOnlyReason).foregroundStyle(.secondary) }
            } else {
                Section("等你决定") {
                    if owner.decisions.isEmpty && owner.dispatches.isEmpty {
                        Text("现在没有等你批准的事。在对话里说「让 Codex 把…」「派个任务去…」，助理拟好会出现在这里。")
                            .foregroundStyle(.secondary)
                    }
                    ForEach(owner.dispatches) { proposal in dispatchRow(proposal) }
                    ForEach(owner.decisions) { decision in decisionRow(decision) }
                }
            }
            goalsSection
            if owner.canWrite {
                Section("我替你做了什么") {
                    if owner.changes.isEmpty { Text("最近没有改动。").foregroundStyle(.secondary) }
                    ForEach(owner.changes) { change in changeRow(change) }
                }
            }
        }
        .navigationTitle("动态")
        .refreshable {
            if let credential = session.credential { await owner.refresh(entry, credential) }
            await session.refreshAndWait(entry)
        }
        .toolbar { RefreshAssistantButton(entry: entry, session: session) }
    }

    private func decisionRow(_ decision: PendingDecision) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("\(decision.title) · \(decision.thread)", systemImage: decision.kind == "interrupt" ? "stop.circle" : "paperplane")
                .font(.headline)
            Text(decision.text.isEmpty ? "（叫停正在跑的这一轮）" : decision.text).textSelection(.enabled)
            if decision.tainted {
                Label("这条是助理读过外部内容后拟的，请看清楚再批", systemImage: "exclamationmark.triangle")
                    .font(.footnote).foregroundStyle(.orange)
            }
            HStack {
                Button("拒绝", role: .destructive) { act(decision, approve: false) }
                Spacer()
                Button("批准（Face ID）") { act(decision, approve: true) }.buttonStyle(.borderedProminent)
            }.disabled(owner.working).buttonStyle(.bordered)
        }.padding(.vertical, 4)
    }

    private func dispatchRow(_ proposal: DispatchProposal) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("任务单 · \(proposal.project ?? "项目")", systemImage: "hammer").font(.headline)
            Text(proposal.title).font(.subheadline.bold())
            Text(proposal.goal).font(.subheadline).textSelection(.enabled).lineLimit(6)
            if !proposal.criteria.isEmpty {
                Text("验收：" + proposal.criteria.joined(separator: "；")).font(.footnote).foregroundStyle(.secondary)
            }
            if proposal.tainted {
                Label("助理读过外部内容后拟的，请到电脑上核对后下发", systemImage: "exclamationmark.triangle")
                    .font(.footnote).foregroundStyle(.orange)
            }
            HStack {
                Button("撤回", role: .destructive) { dispatch(proposal, approve: false) }
                Spacer()
                if !proposal.tainted {
                    Button("下发（Face ID）") { dispatch(proposal, approve: true) }.buttonStyle(.borderedProminent)
                }
            }.disabled(owner.working).buttonStyle(.bordered)
        }.padding(.vertical, 4)
    }

    private func dispatch(_ proposal: DispatchProposal, approve: Bool) {
        guard let credential = session.credential else { return }
        Task { await owner.decideDispatch(entry, credential, proposal, approve: approve) }
    }

    private func changeRow(_ change: OwnerChange) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(change.summary)
            HStack {
                Text(change.statusLabel + (change.tainted ? " · 读过外部内容后" : ""))
                    .font(.footnote).foregroundStyle(.secondary)
                Spacer()
                if change.status == "done" {
                    Button("撤销") { undo(change) }.disabled(owner.working).buttonStyle(.bordered)
                }
            }
            if let detail = change.undoDetail, change.status != "done" {
                Text(([detail] + (change.notRecallable ?? [])).joined(separator: "；"))
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder private var goalsSection: some View {
        Section("目标") {
            if let goals = session.reading.goals.data {
                let active = goals.items.filter { $0.status == "active" }.count
                let blocked = goals.items.filter { $0.status == "blocked" }.count
                NavigationLink { GoalListView(entry: entry, session: session) } label: {
                    LabeledContent("跟进中 \(active) · 有阻塞 \(blocked)", value: "全部")
                }.accessibilityIdentifier("activity-all-goals")
                ForEach(Array(goals.items.filter { ["active", "blocked"].contains($0.status) }.prefix(3))) { goal in
                    NavigationLink { GoalDetail(original: goal, entry: entry, session: session) } label: { GoalRow(goal: goal) }
                }
            } else {
                Text("目标还没读到，下拉刷新。").foregroundStyle(.secondary)
            }
        }
    }

    private func act(_ decision: PendingDecision, approve: Bool) {
        guard let credential = session.credential else { return }
        Task { await owner.decide(entry, credential, decision, approve: approve) }
    }

    private func undo(_ change: OwnerChange) {
        guard let credential = session.credential else { return }
        Task { await owner.undo(entry, credential, change) }
    }
}
