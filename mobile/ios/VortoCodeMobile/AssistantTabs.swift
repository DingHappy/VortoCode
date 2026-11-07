import SwiftUI
import MobileCore

struct AssistantTabs: View {
    let entry: AssistantEntry
    let session: AssistantSession
    @State private var owner = OwnerSession()
    var body: some View {
        // 与 Desktop 同构（ADR-012）：对话为中心；等你决定、我替你做了什么、目标都在「动态」里
        TabView {
            NavigationStack { ChatView(entry: entry, session: session, owner: owner) }
                .tabItem { Label("对话", systemImage: "bubble.left.and.bubble.right") }
            NavigationStack { ActivityView(entry: entry, session: session, owner: owner) }
                .tabItem { Label("动态", systemImage: "list.bullet.rectangle") }
                .badge(owner.decisions.count + owner.dispatches.count + (owner.turn?.pendingCards.count ?? 0))
            NavigationStack { settings.navigationTitle("我") }
                .tabItem { Label("我", systemImage: "person.crop.circle") }
        }
        // 每次登录 / 恢复会话：读写入能力，必要时绑定钥匙、登记推送、读对话
        .task(id: session.credential?.token) {
            owner.resetTimeline()
            owner.sessionExpired = { [session, entry] in session.refresh(entry) }
            if let credential = session.credential { await owner.load(entry, credential) }
        }
    }
    private var settings: some View {
        List {
            Section("这次连接") {
                LabeledContent("助手", value: session.credential?.name ?? "待核对")
                Text(entry.displayAddress).font(.footnote).textSelection(.enabled)
                LabeledContent("权限", value: owner.canWrite ? "对话、随手记、批准与撤销" : "只读助手概览与目标")
                if let until = session.credential?.expiresAt { Text("会话有效至 \(Date(timeIntervalSince1970: Double(until)).formatted())").font(.footnote).foregroundStyle(.secondary) }
            }
            Section("账号") {
                Button("退出手机账号", role: .destructive) { session.logout(entry) }
                    .disabled(session.busy).accessibilityIdentifier("native-logout")
                Text("退出会清除这台手机的安全会话，并请求常驻服务撤销它。电脑登录、其他手机和服务器任务各自保留。")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            Section("权限与记录") {
                if owner.canWrite {
                    NavigationLink("手机权限与审批记录") { OwnerAccessView(entry: entry, session: session) }
                        .accessibilityIdentifier("native-owner-access")
                } else { Text(owner.readOnlyReason).font(.footnote).foregroundStyle(.secondary) }
            }
            Section("来源核对") {
                ReadingStatus(session: session)
                SourceStatus(title: "助手资料", source: session.reading.profile)
                SourceStatus(title: "主聊天", source: session.reading.mainChat)
                SourceStatus(title: "目标", source: session.reading.goals)
                Button("重新核对来源") { session.refresh(entry) }.disabled(session.busy)
                    .accessibilityIdentifier("native-retry-sources")
            }
            Section("关于") {
                LabeledContent("版本", value: "0.3.0")
                Text("原生会话存于设备钥匙串；账号服务的访问和刷新令牌由常驻服务保管。")
                Text("发出的消息和批准由这台手机安全芯片里的钥匙签名，批准 Codex 动作要 Face ID；推送正文加密，只有这台手机能解开。手机发起的对话只能查资料、改你自己的事项和记忆，不能执行命令或对外发送。")
            }.font(.subheadline)
            if !session.pendingLogouts.isEmpty { Section { PendingLogoutView(session: session) } }
        }
    }
    private var refreshButton: some ToolbarContent { RefreshAssistantButton(entry: entry, session: session) }
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
            if !error.isEmpty { Section { Text(error).foregroundStyle(.orange) } }
            if let value = visible {
                Section("手机发起的对话") {
                    Text("可查资料、更新自己的事项和记忆；命令、任意文件写入、操控电脑和直接对外发送不开放。")
                    LabeledContent("普通操作的已有授权", value: value.permissions.effectiveLevel == "reads" ? "可免逐次确认" : "每次询问")
                    Text("读过外部内容后，已有授权失效，仍需你逐次确认。这里只查看规则，不更改权限。")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text("普通卡用日常钥匙；加强确认用 Face ID 批准钥匙。单次批准不扩大手机工具权限。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("最近审批记录") {
                    Text("有限汇总内核决定、App 回合卡片、Codex 动作和任务单，不含操作正文。当前领域记录之外的归档尚未汇总；批准不代表执行完成。")
                        .font(.footnote).foregroundStyle(.secondary)
                    if value.history.status != "current" {
                        Text("审批记录来源待核对，当前列表可能不完整。")
                            .foregroundStyle(.orange)
                    }
                    if !value.history.unavailableLabels.isEmpty {
                        Text("待核对来源：" + value.history.unavailableLabels.joined(separator: "、"))
                            .font(.footnote).foregroundStyle(.orange)
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
        .navigationTitle("权限与审批记录")
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
                    Label("部分来源待核对，旧内容标为历史", systemImage: "clock.badge.exclamationmark").foregroundStyle(.orange)
                }
            } else { Text("尚未取得助手记录").foregroundStyle(.secondary) }
            if !session.note.isEmpty { Text(session.note).foregroundStyle(.orange).font(.subheadline) }
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
                .foregroundStyle(source.freshness == .current ? Color.secondary : .orange)
            if let checked = source.checkedDate {
                Text("上次核对：\(checked.formatted(date: .abbreviated, time: .standard))")
                    .foregroundStyle(.secondary)
            }
        }.font(.footnote)
    }
}
