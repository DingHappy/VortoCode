import SwiftUI
import MobileCore

/// 登录后的外壳（ADR-017 M1，结构对齐 Desktop 的 ADR-016）：全屏主要聊天，不放底部标签栏。
/// 左上打开会话抽屉；居中头像和名字，下面一句话说明后台在做什么，点它打开助理详情（五段同 Desktop 右侧面板）。
/// 文件名沿用 AssistantTabs.swift，避免改动 Xcode 工程文件。
struct AssistantShell: View {
    let entry: AssistantEntry
    let session: AssistantSession
    @State private var owner = OwnerSession()
    @State private var drawerOpen = false
    @State private var detail: AssistantSegment?
    @State private var showSettings = false
    @State private var showSearch = false
    @State private var openTeam: TeamRoute?
    @State private var openSideChat: SideChatRoute?
    @Environment(\.scenePhase) private var scenePhase

    private var name: String? { session.reading.profile.data?.name ?? session.credential?.name }
    private var waiting: [WaitingItem] {
        WaitingItem.collect(cards: owner.turn?.pendingCards ?? [], decisions: owner.decisions, dispatches: owner.dispatches,
                            teams: owner.teams)
    }
    /// 动态里有来源没读到时，「等你处理」的件数可能不全
    private var waitingUncertain: Bool {
        owner.canWrite && (!owner.timelineError.isEmpty || !(owner.timeline?.unavailable.isEmpty ?? false))
    }
    private var statusLine: AssistantStatusLine {
        let goals = session.reading.goals.data.map { $0.items.filter { $0.status == "active" }.count }
        return .make(contactProblem: session.contact.isProblem, canWrite: owner.canWrite,
                     turnRunning: !(owner.turn?.isFinished ?? true), waiting: waiting.count,
                     waitingUncertain: waitingUncertain, activeGoals: goals)
    }

    var body: some View {
        ZStack(alignment: .leading) {
            NavigationStack {
                ChatView(entry: entry, session: session, owner: owner, waiting: waiting,
                         waitingUncertain: waitingUncertain, openActivity: { detail = .feed },
                         openTeam: { openTeam = TeamRoute(id: $0) })
                    .navigationTitle(VCCopy.Nav.mainChat)
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbarBackground(Palette.bg, for: .navigationBar)
                    .toolbar { toolbar }
            }
            // 从左边缘往右拖也能打开抽屉
            Color.clear.frame(width: 16).contentShape(Rectangle())
                .gesture(DragGesture(minimumDistance: 20).onEnded { if $0.translation.width > 60 { drawerOpen = true } })
                .accessibilityHidden(true)
            if drawerOpen {
                Palette.backdrop.ignoresSafeArea().onTapGesture { drawerOpen = false }
                    .accessibilityLabel("关闭会话列表").accessibilityAddTraits(.isButton)
                    .transition(.opacity)
                AssistantDrawer(name: name, contact: session.contact, waiting: waiting.count, statusLine: statusLine,
                                teams: owner.teams, sideChats: owner.sideChats, listNote: owner.conversationsError,
                                writable: owner.canWrite,
                                openTeam: { drawerOpen = false; openTeam = TeamRoute(id: $0) },
                                openSideChat: { drawerOpen = false; openSideChat = SideChatRoute(sid: $0) },
                                close: { drawerOpen = false },
                                openDetail: { drawerOpen = false; detail = .feed },
                                openSearch: { drawerOpen = false; showSearch = true },
                                openSettings: { drawerOpen = false; showSettings = true })
                    .transition(.move(edge: .leading))
                    .gesture(DragGesture(minimumDistance: 20).onEnded { if $0.translation.width < -60 { drawerOpen = false } })
            }
        }
        .animation(.snappy(duration: 0.25), value: drawerOpen)
        .sheet(item: $detail) { segment in
            AssistantDetailView(initial: segment, entry: entry, session: session, owner: owner, name: name, statusLine: statusLine)
        }
        .sheet(isPresented: $showSettings) { NavigationStack { SettingsView(entry: entry, session: session, owner: owner) } }
        .sheet(isPresented: $showSearch) { NavigationStack { ChatSearchView(owner: owner) } }
        .sheet(item: $openTeam) { route in NavigationStack { TeamChatView(teamId: route.id, entry: entry, session: session, owner: owner) } }
        .sheet(item: $openSideChat) { route in NavigationStack { SideChatView(sid: route.sid, entry: entry, session: session) } }
        // 每次登录 / 恢复会话：读写入能力，必要时绑定钥匙、登记推送、读对话
        .task(id: session.credential?.token) {
            owner.resetTimeline()
            owner.sessionExpired = { [session, entry] in session.refresh(entry) }
            if let credential = session.credential { await owner.load(entry, credential) }
        }
        // 前台时每 30 秒刷新一次等你决定、任务单和时间线，「等你处理」不用打开详情也会更新
        .task(id: "\(session.credential?.token ?? ""):\(scenePhase == .active):\(owner.canWrite)") {
            guard scenePhase == .active, owner.canWrite, let credential = session.credential else { return }
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(30)) } catch { return }
                await owner.refresh(entry, credential)
            }
        }
    }

    @ToolbarContentBuilder private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            Button { drawerOpen = true } label: {
                Image(systemName: "line.3.horizontal").font(.body.weight(.medium)).foregroundStyle(Palette.text)
                    .frame(width: 44, height: 44)
                    .overlay(alignment: .topTrailing) {
                        if !waiting.isEmpty { Circle().fill(Palette.waitDot).frame(width: 8, height: 8).offset(x: -8, y: 10) }
                    }
            }
            .accessibilityLabel(waiting.isEmpty ? "打开会话列表" : "打开会话列表，\(waiting.count) 件\(VCCopy.Nav.waiting)")
            .accessibilityIdentifier("native-drawer")
        }
        ToolbarItem(placement: .principal) {
            Button { detail = .feed } label: {
                VStack(spacing: 1) {
                    HStack(spacing: VCSpace.space2) {
                        AssistantAvatar(name: name, size: 22)
                        Text(name ?? "助理").font(.subheadline.weight(.semibold)).foregroundStyle(Palette.text).lineLimit(1)
                    }
                    Text(statusLine.text).font(.caption2).foregroundStyle(Palette.tone(statusLine.tone)).lineLimit(1)
                }.frame(minHeight: 44).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(name ?? "助理")，\(statusLine.text)。打开助理详情")
            .accessibilityIdentifier("native-assistant-header")
        }
        ToolbarItem(placement: .topBarTrailing) {
            Menu {
                Button { session.refresh(entry); Task { if let c = session.credential { await owner.refresh(entry, c); await owner.loadChat(entry, c) } } } label: {
                    Label("刷新", systemImage: "arrow.clockwise")
                }.disabled(session.busy)
                ForEach(AssistantSegment.allCases) { segment in
                    Button(segment.title) { detail = segment }
                }
                Button { showSettings = true } label: { Label(VCCopy.Nav.settings, systemImage: "gearshape") }
            } label: {
                Image(systemName: "ellipsis").font(.body.weight(.medium)).foregroundStyle(Palette.text).frame(width: 44, height: 44)
            }
            .accessibilityLabel("更多").accessibilityIdentifier("native-more")
        }
    }
}

/// 左侧抽屉（对应 Desktop 侧栏）：主要聊天、任务小组、旁聊；底部搜索和设置。
/// 任务小组和旁聊在 M3 接入手机接口之前只说明去电脑上看，不显示假的列表。
struct AssistantDrawer: View {
    let name: String?
    let contact: ContactStatus
    let waiting: Int
    let statusLine: AssistantStatusLine
    let teams: [TeamSummary]
    let sideChats: [SideChatSummary]
    let listNote: String
    let writable: Bool
    let openTeam: (String) -> Void
    let openSideChat: (String) -> Void
    let close: () -> Void
    let openDetail: () -> Void
    let openSearch: () -> Void
    let openSettings: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: VCSpace.space5) {
            Button(action: openDetail) {
                HStack(spacing: VCSpace.space3) {
                    AssistantAvatar(name: name, size: 40)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(name ?? "助理").font(.headline).foregroundStyle(Palette.text)
                        Text(statusLine.text).font(.caption).foregroundStyle(Palette.tone(statusLine.tone)).lineLimit(1)
                    }
                    Spacer(minLength: 0)
                }.contentShape(Rectangle())
            }.buttonStyle(.plain).accessibilityIdentifier("native-drawer-assistant")

            Button(action: close) {
                HStack {
                    Label(VCCopy.Nav.mainChat, systemImage: "bubble.left.and.bubble.right")
                        .font(.subheadline.weight(.medium)).foregroundStyle(Palette.text)
                    Spacer()
                    if waiting > 0 {
                        Text("\(waiting)").font(.caption.weight(.medium)).foregroundStyle(Palette.waitText)
                            .padding(.horizontal, VCSpace.space2).background(Palette.waitBg, in: Capsule())
                    }
                }
                .padding(.horizontal, VCSpace.space3).frame(minHeight: 44)
                .background(Palette.selected, in: RoundedRectangle(cornerRadius: VCRadius.control, style: .continuous))
                .contentShape(Rectangle())
            }.buttonStyle(.plain)
                .accessibilityLabel(waiting > 0 ? "\(VCCopy.Nav.mainChat)，\(waiting) 件\(VCCopy.Nav.waiting)" : VCCopy.Nav.mainChat)
                .accessibilityAddTraits(.isSelected)
                .accessibilityIdentifier("native-drawer-main-chat")

            ScrollView {
                VStack(alignment: .leading, spacing: VCSpace.space5) {
                    if !writable {
                        group(VCCopy.Nav.teams, "这台手机开通对话后，这里会列出任务小组和旁聊。")
                    } else {
                        section(VCCopy.Nav.teams, isEmpty: teams.isEmpty, empty: "现在没有任务小组。") {
                            ForEach(teams) { team in
                                row(title: team.objective, detail: team.statusLabel, waiting: team.needsOwner,
                                    dimmed: !team.isLive) { openTeam(team.id) }
                                    .accessibilityIdentifier("native-drawer-team")
                            }
                        }
                        section(VCCopy.Nav.sideChats, isEmpty: sideChats.isEmpty, empty: "电脑上开的旁聊会列在这里，手机上只能看。") {
                            ForEach(sideChats.prefix(8)) { chat in
                                row(title: chat.title, detail: "\(chat.messages) 条", waiting: false, dimmed: false) { openSideChat(chat.sid) }
                                    .accessibilityIdentifier("native-drawer-side-chat")
                            }
                        }
                        if !listNote.isEmpty { Text(listNote).font(.caption).foregroundStyle(Palette.waitText) }
                    }
                }
            }
            .scrollIndicators(.hidden)

            Label(contact.text(), systemImage: contact.isProblem ? "wifi.exclamationmark" : "desktopcomputer")
                .font(.caption).foregroundStyle(contact.isProblem ? Palette.dangerText : Palette.muted).lineLimit(3)
            HStack(spacing: VCSpace.space2) {
                Button(action: openSettings) { Label(VCCopy.Nav.settings, systemImage: "gearshape").lineLimit(1).fixedSize() }
                    .buttonStyle(VCSecondaryButtonStyle()).accessibilityIdentifier("native-drawer-settings")
                Button(action: openSearch) { Label(VCCopy.Nav.search, systemImage: "magnifyingglass").lineLimit(1).fixedSize() }
                    .buttonStyle(VCSecondaryButtonStyle()).accessibilityIdentifier("native-drawer-search")
            }
        }
        .padding(.horizontal, VCSpace.space4).padding(.vertical, VCSpace.space5)
        .frame(maxWidth: 320, maxHeight: .infinity, alignment: .topLeading)
        .background(Palette.sidebar.ignoresSafeArea())
        .accessibilityElement(children: .contain)
        .accessibilityAddTraits(.isModal)
    }

    @ViewBuilder private func section<Rows: View>(_ title: String, isEmpty: Bool, empty: String,
                                                  @ViewBuilder rows: () -> Rows) -> some View {
        VStack(alignment: .leading, spacing: VCSpace.space1) {
            Text(title).font(.caption.weight(.medium)).foregroundStyle(Palette.muted)
            if isEmpty { Text(empty).font(.footnote).foregroundStyle(Palette.secondary) } else { rows() }
        }
    }

    private func row(title: String, detail: String, waiting: Bool, dimmed: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: VCSpace.space2) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.subheadline).foregroundStyle(dimmed ? Palette.muted : Palette.text).lineLimit(1)
                    Text(detail).font(.caption).foregroundStyle(waiting ? Palette.waitText : Palette.muted).lineLimit(1)
                }
                Spacer(minLength: 0)
                if waiting { Circle().fill(Palette.waitDot).frame(width: 8, height: 8) }
            }
            .padding(.horizontal, VCSpace.space3).frame(minHeight: 44).contentShape(Rectangle())
        }.buttonStyle(.plain)
    }

    private func group(_ title: String, _ note: String) -> some View {
        VStack(alignment: .leading, spacing: VCSpace.space1) {
            Text(title).font(.caption.weight(.medium)).foregroundStyle(Palette.muted)
            Text(note).font(.footnote).foregroundStyle(Palette.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// 助理详情（对应 Desktop 右侧助理面板）：头像名片 + 五段：动态 / 目标 / 审核记录 / 定时 / 身份。
struct AssistantDetailView: View {
    let entry: AssistantEntry
    let session: AssistantSession
    let owner: OwnerSession
    let name: String?
    let statusLine: AssistantStatusLine
    @State private var segment: AssistantSegment
    @Environment(\.dismiss) private var dismiss

    init(initial: AssistantSegment, entry: AssistantEntry, session: AssistantSession, owner: OwnerSession,
         name: String?, statusLine: AssistantStatusLine) {
        self.entry = entry; self.session = session; self.owner = owner; self.name = name; self.statusLine = statusLine
        _segment = State(initialValue: initial)
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: VCSpace.space3) {
                HStack(spacing: VCSpace.space3) {
                    AssistantAvatar(name: name, size: 44)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(name ?? "助理").font(.headline).foregroundStyle(Palette.text)
                        Text(statusLine.text).font(.caption).foregroundStyle(Palette.tone(statusLine.tone)).lineLimit(1)
                    }
                    Spacer()
                }.padding(.horizontal, VCSpace.space4)
                Picker("助理详情", selection: $segment) {
                    ForEach(AssistantSegment.allCases) { item in Text(item.title).tag(item) }
                }
                .pickerStyle(.segmented).padding(.horizontal, VCSpace.space4)
                .accessibilityIdentifier("native-detail-segments")
                content.frame(maxHeight: .infinity)
            }
            .padding(.top, VCSpace.space2)
            .background(Palette.bg)
            .navigationTitle(segment.title).navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("完成") { dismiss() }.accessibilityIdentifier("native-detail-done")
                }
            }
        }
    }

    @ViewBuilder private var content: some View {
        switch segment {
        case .feed:
            ActivityView(entry: entry, session: session, owner: owner)
        case .goals:
            GoalListView(entry: entry, session: session)
        case .audit:
            if owner.canWrite { OwnerAccessView(entry: entry, session: session) }
            else { placeholder("checkmark.shield", owner.readOnlyReason) }
        case .schedule:
            placeholder("clock", "手机上还看不了定时任务。计划和执行记录在电脑上助理面板的「\(VCCopy.Panel.schedule)」里。")
        case .identity:
            IdentityView(entry: entry, session: session, owner: owner, name: name)
        }
    }

    private func placeholder(_ symbol: String, _ text: String) -> some View {
        VStack(spacing: VCSpace.space3) {
            Image(systemName: symbol).font(.title2).foregroundStyle(Palette.muted)
            Text(text).font(.subheadline).foregroundStyle(Palette.secondary).multilineTextAlignment(.center)
        }.padding(VCSpace.space8).frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// 「身份」：这个助理是谁、手机能让它做什么、读到的来源是否有效。性格与记忆的编辑留在电脑上。
struct IdentityView: View {
    let entry: AssistantEntry
    let session: AssistantSession
    let owner: OwnerSession
    let name: String?
    var body: some View {
        List {
            Section("助理") {
                LabeledContent("名字", value: name ?? "待核对")
                Text("性格、关于你和记忆在电脑上助理面板的「\(VCCopy.Panel.identity)」里查看和编辑；手机上还看不了。")
                    .font(.footnote).foregroundStyle(Palette.muted)
            }
            Section("手机能做什么") {
                LabeledContent("权限", value: owner.canWrite ? "对话、随手记、批准与撤销" : "只读助手概览与目标")
                Text("手机发起的对话只能查资料、改你自己的事项和记忆；执行命令、改文件、操控电脑和对外发送要到电脑上做。")
                    .font(.footnote).foregroundStyle(Palette.muted)
            }
            Section("来源核对") {
                ReadingStatus(session: session)
                SourceStatus(title: "助手资料", source: session.reading.profile)
                SourceStatus(title: "主聊天", source: session.reading.mainChat)
                SourceStatus(title: "目标", source: session.reading.goals)
                Button("重新核对来源") { session.refresh(entry) }.disabled(session.busy)
                    .accessibilityIdentifier("native-retry-sources")
            }
        }
        .scrollContentBackground(.hidden).background(Palette.bg)
        .refreshable { await session.refreshAndWait(entry) }
    }
}

/// 设置（抽屉底部）：这次连接、账号、关于。
struct SettingsView: View {
    let entry: AssistantEntry
    let session: AssistantSession
    let owner: OwnerSession
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        List {
            Section("这次连接") {
                LabeledContent("助手", value: session.credential?.name ?? "待核对")
                Text(entry.displayAddress).font(.footnote).textSelection(.enabled)
                if let until = session.credential?.expiresAt {
                    Text("会话有效至 \(Date(timeIntervalSince1970: Double(until)).formatted())").font(.footnote).foregroundStyle(Palette.muted)
                }
            }
            Section("账号") {
                Button("退出手机账号", role: .destructive) { session.logout(entry) }
                    .disabled(session.busy).accessibilityIdentifier("native-logout")
                Text("退出会清除这台手机的安全会话，并请求常驻服务撤销它。电脑登录、其他手机和服务器任务各自保留。")
                    .font(.footnote).foregroundStyle(Palette.muted)
            }
            Section("关于") {
                LabeledContent("版本", value: "0.3.0")
                Text("原生会话存于设备钥匙串；账号服务的访问和刷新令牌由常驻服务保管。")
                Text("发出的消息和批准由这台手机安全芯片里的钥匙签名，批准 Codex 动作要 Face ID；推送正文加密，只有这台手机能解开。手机发起的对话只能查资料、改你自己的事项和记忆，不能执行命令或对外发送。")
            }.font(.subheadline)
            if !session.pendingLogouts.isEmpty { Section { PendingLogoutView(session: session) } }
        }
        .scrollContentBackground(.hidden).background(Palette.bg)
        .navigationTitle(VCCopy.Nav.settings).navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("完成") { dismiss() } } }
    }
}

struct OwnerAccessView: View {
    let entry: AssistantEntry
    let session: AssistantSession
    @State private var snapshot: OwnerAccessSnapshot?
    @State private var source = ""
    @State private var error = ""
    @State private var loading = false
    @State private var request = UUID()
    private var identity: String { entry.displayAddress + ":" + (session.credential?.token ?? "") }
    private var visible: OwnerAccessSnapshot? { source == identity ? snapshot : nil }
    var body: some View {
        List {
            if loading { ProgressView("正在核对权限与记录") }
            if !error.isEmpty { Section { Text(error).foregroundStyle(Palette.waitText) } }
            if let value = visible {
                Section("手机发起的对话") {
                    Text("可查资料、更新自己的事项和记忆；命令、任意文件写入、操控电脑和直接对外发送不开放。")
                    LabeledContent("普通操作的已有授权", value: value.permissions.effectiveLevel == "reads" ? "可免逐次确认" : "每次询问")
                    Text("读过外部内容后，已有授权失效，仍需你逐次确认。这里只查看规则，不更改权限。")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text("普通卡用日常钥匙；加强确认用 Face ID 批准钥匙。单次批准不扩大手机工具权限。")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text("本轮同类授权只在当时回合内生效，历史记录不会恢复授权。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("最近审批记录") {
                    Text("有限汇总内核决定、App 回合卡片、Codex 动作和任务单，不含操作正文。当前领域记录之外的归档尚未汇总；批准不代表执行完成。")
                        .font(.footnote).foregroundStyle(.secondary)
                    if value.history.status != "current" {
                        Text("审批记录来源待核对，当前列表可能不完整。")
                            .foregroundStyle(Palette.waitText)
                    }
                    if !value.history.unavailableLabels.isEmpty {
                        Text("待核对来源：" + value.history.unavailableLabels.joined(separator: "、"))
                            .font(.footnote).foregroundStyle(Palette.waitText)
                    }
                    if value.history.entries.isEmpty {
                        Text(value.history.status == "current" ? "最近读取范围内没有审批记录。" : "暂时无法确认审批记录。")
                            .foregroundStyle(.secondary)
                    }
                    ForEach(value.history.entries) { row in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(row.label + " · " + row.outcomeLabel).font(.headline)
                            Text(row.sourceLabel + " · " + row.scopeLabel).font(.subheadline)
                            Text(row.recordLabel + " · " + row.originLabel + " · " + (row.ts.isEmpty ? "审批时点待核对" : row.ts))
                                .font(.caption).foregroundStyle(.secondary)
                            if row.decisionMetadataMissing == true { Text("审批来源与时点没有完整记录，请人工核对。").font(.caption).foregroundStyle(.secondary) }
                            if let status = row.sourceStatusLabel { Text("当前来源状态：" + status).font(.caption).foregroundStyle(.secondary) }
                            if row.tainted { Text("读过外部内容后").font(.caption).foregroundStyle(.secondary) }
                        }
                    }
                }
                Section { Text("读取时间：" + value.generatedAt).font(.footnote).foregroundStyle(.secondary) }
            } else if !loading && error.isEmpty { Text("尚未核对权限与记录。") }
        }
        
        .task(id: identity) { snapshot = nil; source = ""; await refresh() }
        .refreshable { await refresh() }
        .toolbar { Button("刷新") { Task { await refresh() } }.disabled(loading) }
    }
    @MainActor private func refresh() async {
        guard let credential = session.credential else { return }
        let expected = identity, attempt = UUID()
        request = attempt; loading = true; error = ""
        defer { if request == attempt { loading = false } }
        do {
            let value = try await MobileAPI(entry: entry).ownerAccess(credential)
            guard request == attempt, identity == expected, !Task.isCancelled else { return }
            snapshot = value; source = expected
        } catch {
            guard request == attempt, identity == expected, !Task.isCancelled else { return }
            snapshot = nil; source = ""
            self.error = "权限与审批记录暂时读不到，请刷新核对。"
        }
    }
}

struct ReadingStatus: View {
    let session: AssistantSession
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if session.busy { HStack { ProgressView(); Text(session.phase.label) } }
            else if session.reading.instanceId != nil {
                if session.reading.allCurrent {
                    Label("各来源最近读取有效", systemImage: "checkmark.shield")
                } else {
                    Label("部分来源待核对，旧内容标为历史", systemImage: "clock.badge.exclamationmark").foregroundStyle(Palette.waitText)
                }
            } else { Text("尚未取得助手记录").foregroundStyle(.secondary) }
            if !session.note.isEmpty { Text(session.note).foregroundStyle(Palette.waitText).font(.subheadline) }
        }.accessibilityIdentifier("native-freshness")
    }
}

struct RefreshAssistantButton: ToolbarContent {
    let entry: AssistantEntry
    let session: AssistantSession
    var body: some ToolbarContent {
        ToolbarItem(placement: .topBarTrailing) {
            Button { session.refresh(entry) } label: { Image(systemName: "arrow.clockwise") }
                .disabled(session.busy).accessibilityLabel("刷新助手").accessibilityIdentifier("native-refresh")
        }
    }
}

struct SourceStatus<Value: Codable & Sendable>: View {
    let title: String
    let source: ReadSource<Value>
    private var state: String {
        switch source.freshness {
        case .current: return "最近读取有效"
        case .historical: return "历史内容，等待重新核对"
        case .unavailable: return "来源待核对"
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("\(title)：\(state)", systemImage: source.freshness == .current ? "checkmark.shield" : "clock.badge.exclamationmark")
                .foregroundStyle(source.freshness == .current ? Palette.muted : Palette.waitText)
            if let checked = source.checkedDate {
                Text("上次核对：\(checked.formatted(date: .abbreviated, time: .standard))")
                    .foregroundStyle(.secondary)
            }
        }.font(.footnote)
    }
}


// MARK: - 任务小组与旁聊（ADR-017 M3）

struct TeamRoute: Identifiable { let id: String }
struct SideChatRoute: Identifiable { let sid: String; var id: String { sid } }

/// 小组群聊（对应 Desktop 的 TeamChat）：成员、经过、交付与原文。在群里说话、验收 / 退回要 Face ID，停止用日常钥匙。
/// 成员读过外部内容时，相关消息标出来，只当纯文本显示。
struct TeamChatView: View {
    let teamId: String
    let entry: AssistantEntry
    let session: AssistantSession
    let owner: OwnerSession
    @State private var chat: TeamChat?
    @State private var loadError = ""
    @State private var draft = ""

    init(teamId: String, entry: AssistantEntry, session: AssistantSession, owner: OwnerSession, initial: TeamChat? = nil) {
        self.teamId = teamId; self.entry = entry; self.session = session; self.owner = owner
        _chat = State(initialValue: initial)
    }
    @State private var rejecting = false
    @State private var reason = ""
    @State private var confirmStop = false
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        ScrollViewReader { proxy in
            VStack(spacing: 0) {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: VCSpace.space4) {
                        if let chat {
                            header(chat)
                            ForEach(Array(chat.messages.enumerated()), id: \.offset) { _, message in bubble(message) }
                        } else if loadError.isEmpty {
                            ProgressView("正在读取群聊…").frame(maxWidth: .infinity).padding(.top, VCSpace.space8)
                        }
                        if !loadError.isEmpty { Text(loadError).font(.footnote).foregroundStyle(Palette.waitText) }
                        Color.clear.frame(height: 1).id("bottom")
                    }
                    .padding(.horizontal, VCSpace.space4).padding(.vertical, VCSpace.space3)
                }
                .refreshable { await load() }
                .onChange(of: chat?.messages.count) { _, _ in withAnimation { proxy.scrollTo("bottom") } }
                if !owner.note.isEmpty {
                    Text(owner.note).font(.footnote).foregroundStyle(Palette.muted)
                        .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, VCSpace.space4).padding(.top, VCSpace.space2)
                }
                if let chat { actions(chat) }
            }
        }
        .background(Palette.bg)
        .navigationTitle(VCCopy.Nav.teams).navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() } }
            if chat?.team.isLive == true {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("停止小组", role: .destructive) { confirmStop = true }
                    } label: { Image(systemName: "ellipsis") }.accessibilityLabel("更多")
                }
            }
        }
        .confirmationDialog("停止这个小组？", isPresented: $confirmStop, titleVisibility: .visible) {
            Button("停止", role: .destructive) { run(.stop) }
        } message: { Text("在做的成员会被叫停；已经做的不会撤回。") }
        // 打开着就每 5 秒读一次，小组在做时能看到新进展
        .task(id: "\(teamId):\(scenePhase == .active)") {
            guard scenePhase == .active else { return }
            while !Task.isCancelled {
                await load()
                do { try await Task.sleep(for: .seconds(5)) } catch { return }
            }
        }
    }

    private func header(_ chat: TeamChat) -> some View {
        VStack(alignment: .leading, spacing: VCSpace.space2) {
            HStack(spacing: VCSpace.space2) {
                StatusPill(text: TeamStatus.label(chat.team.status), kind: pillKind(chat.team.status))
                if let project = chat.team.project { Text(project).font(.caption).foregroundStyle(Palette.muted) }
            }
            Text(chat.team.objective).font(.headline).foregroundStyle(Palette.text).textSelection(.enabled)
            if let acceptance = chat.team.acceptance, !acceptance.isEmpty {
                Text("验收标准：" + acceptance.joined(separator: "；")).font(.footnote).foregroundStyle(Palette.secondary)
            }
            if !chat.members.isEmpty {
                Text(chat.members.map { $0.name + ($0.working ? "（在做）" : "") }.joined(separator: " · "))
                    .font(.caption).foregroundStyle(Palette.muted)
            }
            if chat.team.tainted == true {
                Label("成员读过外部内容，标了提醒的消息请核对原文再决定", systemImage: "exclamationmark.triangle")
                    .font(.footnote).foregroundStyle(Palette.waitText)
            }
        }.vcCard()
    }

    @ViewBuilder private func bubble(_ message: TeamChat.Message) -> some View {
        if message.isSystem {
            Text(message.text).font(.caption).foregroundStyle(Palette.muted).frame(maxWidth: .infinity)
                .multilineTextAlignment(.center)
        } else if message.fromOwner {
            HStack {
                Spacer(minLength: VCSpace.space10)
                Text(message.text).font(.body).foregroundStyle(Palette.text).textSelection(.enabled)
                    .padding(.horizontal, VCSpace.space4).padding(.vertical, VCSpace.space2 + 2)
                    .background(Palette.fill, in: RoundedRectangle(cornerRadius: VCRadius.bubble, style: .continuous))
            }
        } else {
            VStack(alignment: .leading, spacing: VCSpace.space1) {
                HStack(spacing: VCSpace.space2) {
                    Text(message.speakerLabel).font(.caption.weight(.medium)).foregroundStyle(Palette.secondary)
                    if message.mention == "owner" { StatusPill(text: VCCopy.Status.waiting, kind: .waiting) }
                    if message.kind == "submitted" { StatusPill(text: "交付", kind: .done) }
                }
                CollapsibleText(text: message.text)
                if message.tainted == true {
                    Text("含外部内容，请核对").font(.caption).foregroundStyle(Palette.waitText)
                }
                if let evidence = message.evidence, !evidence.isEmpty {
                    VStack(alignment: .leading, spacing: VCSpace.space2) {
                        Text("交付与原文").font(.caption.weight(.medium)).foregroundStyle(Palette.muted)
                        ForEach(Array(evidence.enumerated()), id: \.offset) { _, item in
                            VStack(alignment: .leading, spacing: 2) {
                                if let text = item.text, !text.isEmpty { Text(text).font(.footnote.weight(.medium)).foregroundStyle(Palette.text) }
                                if let proof = item.evidence, !proof.isEmpty { Text(proof).font(.footnote).foregroundStyle(Palette.secondary).textSelection(.enabled) }
                            }
                        }
                    }.vcCard(padding: VCSpace.space3)
                }
            }
        }
    }

    @ViewBuilder private func actions(_ chat: TeamChat) -> some View {
        VStack(alignment: .leading, spacing: VCSpace.space2) {
            switch chat.team.status {
            case "review":
                if rejecting {
                    TextField("写明要改什么", text: $reason, axis: .vertical).lineLimit(1...4)
                        .padding(VCSpace.space3)
                        .background(Palette.surface, in: RoundedRectangle(cornerRadius: VCRadius.control, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: VCRadius.control, style: .continuous).strokeBorder(Palette.border))
                    HStack {
                        Button("取消") { rejecting = false; reason = "" }.buttonStyle(VCSecondaryButtonStyle())
                        Spacer()
                        Button("退回（Face ID）") { run(.reject(reason)) }.buttonStyle(VCPrimaryButtonStyle())
                            .disabled(reason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || owner.working)
                    }
                } else {
                    HStack {
                        Button("退回…") { rejecting = true }.buttonStyle(VCSecondaryButtonStyle(danger: true))
                        Spacer()
                        Button("验收通过（Face ID）") { run(.accept) }.buttonStyle(VCPrimaryButtonStyle())
                            .accessibilityIdentifier("native-team-accept")
                    }.disabled(owner.working)
                }
            case "active", "waiting":
                if chat.team.status == "waiting", let reason = chat.team.waitingReason, !reason.isEmpty, chat.team.tainted != true {
                    Text("小组在等你：" + reason).font(.caption).foregroundStyle(Palette.waitText).lineLimit(3)
                }
                HStack(alignment: .bottom, spacing: VCSpace.space2) {
                    TextField(chat.team.status == "waiting" ? "回复小组…" : "在群里说…", text: $draft, axis: .vertical)
                        .lineLimit(1...4).padding(.vertical, VCSpace.space3).padding(.horizontal, VCSpace.space3)
                    Button { run(.message(draft)) } label: {
                        Image(systemName: "arrow.up").font(.body.weight(.semibold)).frame(width: 36, height: 36)
                            .foregroundStyle(Palette.onInk)
                            .background(canSend ? Palette.ink : Palette.muted.opacity(0.4), in: Circle())
                    }.frame(width: 44, height: 44).disabled(!canSend)
                        .accessibilityLabel("发送（需要 Face ID）").accessibilityIdentifier("native-team-send")
                }
                .background(Palette.surface, in: RoundedRectangle(cornerRadius: VCRadius.input, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: VCRadius.input, style: .continuous).strokeBorder(Palette.border))
                Text("发出会叫醒小组接着做，需要 Face ID。").font(.caption).foregroundStyle(Palette.muted)
            default:
                Text("小组已结束（\(TeamStatus.label(chat.team.status))），只能查看。").font(.footnote).foregroundStyle(Palette.muted)
            }
        }
        .padding(.horizontal, VCSpace.space3).padding(.vertical, VCSpace.space2)
        .background(Palette.bg)
    }

    private var canSend: Bool {
        owner.canWrite && !owner.working && session.credential != nil
            && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func pillKind(_ status: String) -> StatusPill.Kind {
        switch status {
        case "active": return .running
        case "waiting", "review": return .waiting
        case "done": return .done
        case "failed": return .failed
        default: return .unknown
        }
    }

    private func run(_ action: OwnerSession.TeamAction) {
        guard let credential = session.credential else { return }
        Task {
            if await owner.act(on: teamId, action, entry, credential) {
                if case .message = action { draft = "" }
                if case .reject = action { rejecting = false; reason = "" }
                await load()
            }
        }
    }

    @MainActor private func load() async {
        guard let credential = session.credential else { return }
        do {
            let value = try await MobileAPI(entry: entry).team(credential, id: teamId)
            guard !Task.isCancelled else { return }
            chat = value
            loadError = ""
        } catch {
            guard !Task.isCancelled else { return }
            loadError = chat == nil ? "群聊暂时读不到，下拉重试。" : "最新进展暂时读不到，下面是上次的内容。"
        }
    }
}

/// 很长的成员结果先显示开头，点「展开全文」看完整的。
struct CollapsibleText: View {
    let text: String
    @State private var expanded = false
    private var long: Bool { text.count > 600 }
    var body: some View {
        VStack(alignment: .leading, spacing: VCSpace.space1) {
            Text(long && !expanded ? String(text.prefix(600)) + "…" : text)
                .font(.body).foregroundStyle(Palette.text).textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
            if long {
                Button(expanded ? "收起" : "展开全文") { expanded.toggle() }.font(.caption).foregroundStyle(Palette.secondary)
            }
        }
    }
}

/// 旁聊（Desktop 上开的其它会话）：手机上只读。接着聊请在电脑上，或在主要聊天里说。
struct SideChatView: View {
    let sid: String
    let entry: AssistantEntry
    let session: AssistantSession
    @State private var transcript: SideChatTranscript?
    @State private var loadError = ""
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: VCSpace.space4) {
                Text("旁聊在手机上只能看。要接着聊，请在电脑上打开这段，或者在主要聊天里说。")
                    .font(.footnote).foregroundStyle(Palette.muted).vcCard(padding: VCSpace.space3)
                if let transcript {
                    ForEach(Array(transcript.items.enumerated()), id: \.offset) { _, item in
                        if item.isMine {
                            HStack {
                                Spacer(minLength: VCSpace.space10)
                                Text(item.text).font(.body).foregroundStyle(Palette.text).textSelection(.enabled)
                                    .padding(.horizontal, VCSpace.space4).padding(.vertical, VCSpace.space2 + 2)
                                    .background(Palette.fill, in: RoundedRectangle(cornerRadius: VCRadius.bubble, style: .continuous))
                            }
                        } else {
                            Text(item.text).font(.body).foregroundStyle(Palette.text).textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading).lineSpacing(3)
                        }
                    }
                } else if loadError.isEmpty {
                    ProgressView("正在读取…").frame(maxWidth: .infinity)
                }
                if !loadError.isEmpty { Text(loadError).font(.footnote).foregroundStyle(Palette.waitText) }
            }
            .padding(.horizontal, VCSpace.space4).padding(.vertical, VCSpace.space3)
        }
        .background(Palette.bg)
        .navigationTitle(transcript?.title ?? VCCopy.Nav.sideChats).navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() } } }
        .refreshable { await load() }
        .task(id: sid) { await load() }
    }

    @MainActor private func load() async {
        guard let credential = session.credential else { return }
        do {
            transcript = try await MobileAPI(entry: entry).sideChat(credential, sid: sid)
            loadError = ""
        } catch {
            loadError = "这段旁聊暂时读不到，下拉重试。"
        }
    }
}
