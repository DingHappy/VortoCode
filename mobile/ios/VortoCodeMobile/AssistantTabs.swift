import SwiftUI
import MobileCore

struct AssistantTabs: View {
    let entry: AssistantEntry
    let session: AssistantSession
    var body: some View {
        TabView {
            NavigationStack { overview.navigationTitle("我的助手") }
                .tabItem { Label("概览", systemImage: "square.grid.2x2") }
            NavigationStack { GoalListView(entry: entry, session: session) }
                .tabItem { Label("目标", systemImage: "checklist") }
            NavigationStack { settings.navigationTitle("设置") }
                .tabItem { Label("设置", systemImage: "gearshape") }
        }
    }
    private var overview: some View {
        List {
            Section {
                HStack(spacing: 14) {
                    Image("Brand").resizable().scaledToFit().frame(width: 48, height: 48).clipShape(RoundedRectangle(cornerRadius: 12))
                    VStack(alignment: .leading, spacing: 6) {
                        Text(session.reading.profile.data?.name ?? session.credential?.name ?? "常驻助手").font(.title3.bold())
                        Text(entry.host).font(.footnote).foregroundStyle(.secondary)
                    }
                }.padding(.vertical, 8)
                ReadingStatus(session: session)
            }
            if let goals = session.reading.goals.data {
                Section("目标中的进展") {
                    SourceStatus(title: "目标", source: session.reading.goals)
                    LabeledContent("跟进中", value: String(goals.items.filter { $0.status == "active" }.count))
                    LabeledContent("有阻塞", value: String(goals.items.filter { $0.status == "blocked" }.count))
                    LabeledContent("原记录已达成", value: String(goals.items.filter { $0.status == "achieved" }.count))
                }
                Section("下一步") {
                    let actionable = goals.items.filter { ["active","blocked"].contains($0.status) }.prefix(5)
                    if actionable.isEmpty { Text("当前读取的目标中没有跟进项。").foregroundStyle(.secondary) }
                    ForEach(Array(actionable)) { goal in NavigationLink { GoalDetail(original: goal, entry: entry, session: session) } label: { GoalRow(goal: goal) } }
                }
            } else { Section { Text("目标来源尚未取得，不能据此判断是否有待办。").foregroundStyle(.secondary) } }
            Section {
                SourceStatus(title: "主聊天", source: session.reading.mainChat)
                if let chat = session.reading.mainChat.data {
                    LabeledContent("原记录", value: chat.sid != nil ? "已登记" : "尚未登记")
                }
                Text("手机当前只读，聊天正文、任务批准与电脑接管尚未接入。不会根据目标数量推断今天的任务完成情况。")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }.refreshable { await session.refreshAndWait(entry) }.toolbar { refreshButton }
    }
    private var settings: some View {
        List {
            Section("这次连接") {
                LabeledContent("助手", value: session.credential?.name ?? "待核对")
                Text(entry.displayAddress).font(.footnote).textSelection(.enabled)
                LabeledContent("权限", value: "只读助手概览与目标")
                if let until = session.credential?.expiresAt { Text("会话有效至 \(Date(timeIntervalSince1970: Double(until)).formatted())").font(.footnote).foregroundStyle(.secondary) }
            }
            Section("账号") {
                Button("退出手机账号", role: .destructive) { session.logout(entry) }
                    .disabled(session.busy).accessibilityIdentifier("native-logout")
                Text("退出会清除这台手机的安全会话，并请求常驻服务撤销它。电脑登录、其他手机和服务器任务各自保留。")
                    .font(.footnote).foregroundStyle(.secondary)
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
                LabeledContent("版本", value: "0.2.0")
                Text("原生会话存于设备钥匙串；账号服务的访问和刷新令牌由常驻服务保管。")
                Text("此版本没有手机后台任务、推送、语音或审批能力。")
            }.font(.subheadline)
            if !session.pendingLogouts.isEmpty { Section { PendingLogoutView(session: session) } }
        }
    }
    private var refreshButton: some ToolbarContent { RefreshAssistantButton(entry: entry, session: session) }
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
