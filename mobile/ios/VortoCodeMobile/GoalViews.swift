import SwiftUI
import MobileCore

struct GoalListView: View {
    let entry: AssistantEntry
    let session: AssistantSession
    @State private var query = ""
    @State private var searching = false
    @State private var filter = GoalFilter.all

    private var filtered: [Goal] {
        let terms = query.split(whereSeparator: \.isWhitespace).map(String.init)
        return (session.reading.goals.data?.items ?? []).filter { goal in
            let text = [goal.objective, goal.nextAction, goal.blocker].joined(separator: "\n")
            return filter.matches(goal) && terms.allSatisfy { text.localizedStandardContains($0) }
        }
    }
    private var hasFilters: Bool { filter != .all || !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    var body: some View {
        List {
            Section {
                ReadingStatus(session: session)
                SourceStatus(title: "目标", source: session.reading.goals)
                if let goals = session.reading.goals.data {
                    Text("显示 \(filtered.count) / \(goals.items.count) 条已读取目标")
                        .font(.footnote).foregroundStyle(.secondary).accessibilityIdentifier("goal-read-scope")
                    if filter != .all { Text("筛选：\(filter.title)").font(.footnote).accessibilityIdentifier("goal-selected-filter") }
                }
                if hasFilters { Button("清除搜索与筛选") { query = ""; filter = .all; searching = false }.accessibilityIdentifier("goal-clear-filters") }
            }
            if let goals = session.reading.goals.data {
                if goals.items.isEmpty {
                    ContentUnavailableView("原记录中没有目标", systemImage: "checklist", description: Text(session.reading.goals.freshness == .current ? "最近读取的原记录中没有目标。" : "上次读取时没有目标，当前等待重新核对。"))
                } else if filtered.isEmpty {
                    ContentUnavailableView("没有匹配的目标", systemImage: "magnifyingglass", description: Text("搜索与筛选只作用于本次已读取的目标，不表示服务器上没有其他目标。"))
                } else {
                    ForEach(filtered) { goal in
                        NavigationLink { GoalDetail(original: goal, entry: entry, session: session) } label: { GoalRow(goal: goal) }
                    }
                }
                if goals.truncated { Text("只读取了最近 100 条，搜索与筛选不包含更早的记录。请在电脑端查看完整目标。")
                    .font(.footnote).foregroundStyle(.secondary) }
            } else {
                ContentUnavailableView("目标来源待核对", systemImage: "wifi.exclamationmark", description: Text("请刷新或检查常驻服务。来源不可用不表示没有目标。"))
            }
        }
        .navigationTitle("目标")
        .searchable(text: $query, isPresented: $searching, placement: .navigationBarDrawer(displayMode: .always), prompt: "搜索目标、下一步或阻塞")
        .refreshable { await session.refreshAndWait(entry) }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Menu {
                    Picker("目标状态", selection: $filter) {
                        ForEach(GoalFilter.allCases) { item in Text(item.title).tag(item) }
                    }
                } label: { Label("状态", systemImage: filter == .all ? "line.3.horizontal.decrease.circle" : "line.3.horizontal.decrease.circle.fill") }
                    .accessibilityLabel("筛选目标状态").accessibilityIdentifier("goal-filter")
            }
            RefreshAssistantButton(entry: entry, session: session)
        }
    }
}

private enum GoalFilter: String, CaseIterable, Identifiable {
    case all, active, blocked, achieved, paused, cancelled, unknown
    var id: String { rawValue }
    var title: String {
        switch self {
        case .all: return "全部状态"
        case .active: return "跟进中"
        case .blocked: return "有阻塞"
        case .achieved: return "已达成"
        case .paused: return "已暂停"
        case .cancelled: return "已取消"
        case .unknown: return "待核对状态"
        }
    }
    func matches(_ goal: Goal) -> Bool {
        if self == .all { return true }
        if self == .unknown {
            return ![GoalFilter.active, .blocked, .achieved, .paused, .cancelled].contains { $0.rawValue == goal.status }
        }
        return rawValue == goal.status
    }
}

struct GoalRow: View {
    let goal: Goal
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(goal.objective).font(.headline)
            Text(goal.statusLabel).font(.caption).foregroundStyle(goal.status == "blocked" ? .orange : .secondary)
            if !goal.nextAction.isEmpty { Text("下一步：\(goal.nextAction)").font(.subheadline).foregroundStyle(.secondary).lineLimit(3) }
        }.padding(.vertical, 5)
    }
}

struct GoalDetail: View {
    let entry: AssistantEntry
    let session: AssistantSession
    @State private var lastKnown: Goal

    init(original: Goal, entry: AssistantEntry, session: AssistantSession) {
        self.entry = entry; self.session = session; _lastKnown = State(initialValue: original)
    }
    private var current: Goal? { session.reading.goals.data?.items.first { $0.id == lastKnown.id } }
    private var goal: Goal { current ?? lastKnown }
    private var historical: Bool { current == nil || session.reading.goals.freshness != .current }

    var body: some View {
        List {
            Section {
                Text(goal.objective).font(.title3.bold()).textSelection(.enabled)
                LabeledContent("原记录状态", value: goal.statusLabel)
                if session.busy || !session.note.isEmpty { ReadingStatus(session: session) }
                if historical { Label("历史内容，刷新后重新核对", systemImage: "clock").foregroundStyle(.orange).accessibilityIdentifier("goal-history") }
                if current == nil, session.reading.goals.freshness == .current {
                    Text("最新读取结果中未找到此目标，以下保留最后查看的记录。列表最多读取 100 条，不能据此判定目标已删除或已完成。")
                        .font(.footnote).foregroundStyle(.secondary).accessibilityIdentifier("goal-not-in-read")
                }
                SourceStatus(title: "目标列表", source: session.reading.goals)
            }
            Section("下一步") { Text(goal.nextAction.isEmpty ? "原记录尚未填写下一步。" : goal.nextAction).textSelection(.enabled).accessibilityIdentifier("goal-next-action") }
            Section("阻塞") { Text(goal.blocker.isEmpty ? "原记录未填写阻塞。" : goal.blocker).textSelection(.enabled) }
            Section { LabeledContent("记录版本", value: String(goal.revision)); Text("沿用原目标状态；此页面不修改或验收目标。").font(.footnote).foregroundStyle(.secondary) }
        }
        .navigationTitle("目标详情").navigationBarTitleDisplayMode(.inline)
        .refreshable { await session.refreshAndWait(entry) }
        .toolbar { RefreshAssistantButton(entry: entry, session: session) }
        .onChange(of: current?.revision, initial: true) { _, _ in if let current { lastKnown = current } }
    }
}
