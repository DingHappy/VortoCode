import SwiftUI
import MobileCore

/// 等你批准的 Codex 动作。卡片上展示的对象和正文，就是批准时签进去的那一条（服务端按摘要核对）。
struct DecisionsView: View {
    let entry: AssistantEntry
    let session: AssistantSession
    let owner: OwnerSession
    var body: some View {
        List {
            if !owner.note.isEmpty { Section { Text(owner.note).font(.subheadline).foregroundStyle(.secondary) } }
            if !owner.canWrite {
                Section { Text(owner.readOnlyReason).foregroundStyle(.secondary) }
            } else if owner.decisions.isEmpty {
                Section { Text("现在没有等你批准的动作。").foregroundStyle(.secondary) }
            }
            ForEach(owner.decisions) { decision in
                Section(decision.thread) {
                    Label(decision.title, systemImage: decision.kind == "interrupt" ? "stop.circle" : "paperplane")
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
                }
            }
            Section {
                Text("批准后只代表交给了 Codex，是否做完以 Codex 会话的最新状态为准。批准需要 Face ID，由这台手机的安全芯片签名。")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .navigationTitle("等你决定")
        .refreshable { if let credential = session.credential { await owner.refresh(entry, credential) } }
    }
    private func act(_ decision: PendingDecision, approve: Bool) {
        guard let credential = session.credential else { return }
        Task { await owner.decide(entry, credential, decision, approve: approve) }
    }
}

/// 随手记 + 我替你做了什么（逐条可撤销）。
struct CaptureView: View {
    let entry: AssistantEntry
    let session: AssistantSession
    @Bindable var owner: OwnerSession
    var body: some View {
        List {
            Section("随手记") {
                TextField("比如：周五前交周报", text: $owner.draft, axis: .vertical)
                    .lineLimit(2...5).disabled(!owner.canWrite || owner.working)
                Button("记下") { submit() }
                    .disabled(!owner.canWrite || owner.working || owner.draft.trimmingCharacters(in: .whitespaces).isEmpty)
                Text(owner.canWrite ? "记成你的一个目标，之后可以让助理整理；记错了在下面撤销。" : owner.readOnlyReason)
                    .font(.footnote).foregroundStyle(.secondary)
            }
            if !owner.note.isEmpty { Section { Text(owner.note).font(.subheadline).foregroundStyle(.secondary) } }
            Section("我替你做了什么") {
                if owner.changes.isEmpty { Text("最近没有改动。").foregroundStyle(.secondary) }
                ForEach(owner.changes) { change in
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
            }
        }
        .navigationTitle("随手记")
        .refreshable { if let credential = session.credential { await owner.refresh(entry, credential) } }
    }
    private func submit() {
        guard let credential = session.credential else { return }
        Task { await owner.capture(entry, credential) }
    }
    private func undo(_ change: OwnerChange) {
        guard let credential = session.credential else { return }
        Task { await owner.undo(entry, credential, change) }
    }
}
