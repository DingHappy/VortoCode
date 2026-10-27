import SwiftUI
import MobileCore

struct HomeView: View {
    @AppStorage("vortocode.mobile.entry-origin") private var savedOrigin = ""
    @State private var editor: EditorRoute?
    @State private var session = AssistantSession()
    @Environment(\.scenePhase) private var scenePhase

    private var entry: AssistantEntry? { try? EntryPolicy.parse(savedOrigin) }

    var body: some View {
        Group {
            if let entry, session.credential != nil {
                AssistantTabs(entry: entry, session: session)
            } else {
                welcome
            }
        }
        .task(id: savedOrigin) {
            if let entry { session.restore(entry) } else { session.stop(); session.credential = nil; session.reading = MobileReadState() }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active, let entry { session.refresh(entry) }
            if phase == .background { session.suspendReads() }
        }
        .sheet(item: $editor) { _ in ConnectionView(savedOrigin: $savedOrigin) }
    }

    private var welcome: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    HStack(spacing: 12) {
                        Image("Brand").resizable().scaledToFit().frame(width: 44, height: 44)
                            .clipShape(RoundedRectangle(cornerRadius: 12)).accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 3) {
                            Text("VortoCode").font(.headline)
                            Text("你的常驻助手").font(.subheadline).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button { session.stop(); editor = .connection } label: { Image(systemName: "slider.horizontal.3").frame(width: 44, height: 44) }
                            .accessibilityLabel("连接设置").accessibilityIdentifier("entry-settings")
                            .disabled(session.busy)
                    }
                    VStack(alignment: .leading, spacing: 16) {
                        Text("随时交接，\n随处跟进。")
                            .font(.system(.largeTitle, design: .rounded, weight: .bold)).fixedSize(horizontal: false, vertical: true)
                        Text("用同一个 VortoCode 账号，查看常驻助手的目标与进展。合上手机，服务器上的工作仍由助手继续负责。")
                            .foregroundStyle(.secondary).lineSpacing(5)
                    }
                    connectionCard
                    if !session.note.isEmpty { Text(session.note).font(.subheadline).foregroundStyle(.orange).accessibilityIdentifier("session-note") }
                    if !session.pendingLogouts.isEmpty { PendingLogoutView(session: session) }
                    VStack(alignment: .leading, spacing: 22) {
                        capability("person.crop.circle.badge.checkmark", "同账号登录", "沿用 VortoCode 账号，日常登录无需手动核对码。")
                        capability("checklist", "查看目标与下一步", "读取原助手记录，保留状态、阻塞和核对时间。")
                        capability("lock.shield", "权限清楚", "当前为只读入口，聊天、审批和电脑接管尚未接入手机。")
                    }
                    Text("iPhone · 0.2.0\n当前为原生只读版。登录后直接查看概览与目标；服务是否可用，以最近读取结果为准。")
                        .font(.footnote).foregroundStyle(.secondary).lineSpacing(4)
                }.padding(24).frame(maxWidth: 600)
                    .frame(maxWidth: .infinity)
            }
            .background(Color(uiColor: .systemGroupedBackground))
            .toolbar(.hidden, for: .navigationBar)

        }
    }

    private var connectionCard: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label(entry == nil ? "连接你的助手" : "已保存助手入口", systemImage: "desktopcomputer")
                .font(.headline)
            if let entry {
                Text(entry.displayAddress).font(.subheadline).textSelection(.enabled)
                    .foregroundStyle(.secondary).accessibilityIdentifier("saved-entry")
                Button { session.login(entry) } label: {
                    Label(session.busy ? session.phase.label : "登录并打开助手", systemImage: "person.crop.circle.badge.checkmark").frame(maxWidth: .infinity, minHeight: 36)
                }.buttonStyle(.borderedProminent).controlSize(.large).accessibilityIdentifier("open-assistant").disabled(session.busy)
                Text("登录在系统认证窗口完成，会话安全保存在这台设备。退出与刷新在 App 内处理。")
                    .font(.footnote).foregroundStyle(.secondary)
            } else {
                Text("在桌面端关联账号后，把设置中的手机入口地址填到这里。")
                    .foregroundStyle(.secondary)
                Button { editor = .connection } label: {
                    Label("连接常驻助手", systemImage: "plus").frame(maxWidth: .infinity, minHeight: 36)
                }.buttonStyle(.borderedProminent).controlSize(.large).accessibilityIdentifier("connect-assistant")
            }
        }.padding(20).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 24))
    }

    private func capability(_ symbol: String, _ title: String, _ detail: String) -> some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: symbol).font(.title3).foregroundStyle(.blue).frame(width: 28)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.headline)
                Text(detail).font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

private enum EditorRoute: String, Identifiable {
    case connection
    var id: String { rawValue }
}

#Preview { HomeView() }
