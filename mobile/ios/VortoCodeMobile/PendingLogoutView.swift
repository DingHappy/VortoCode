import SwiftUI

struct PendingLogoutView: View {
    let session: AssistantSession
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("远端退出待确认", systemImage: "lock.rotation").font(.headline)
            Text("以下旧会话已不用于本机登录，但远端撤销尚未确认。可以手动重试。")
                .font(.subheadline).foregroundStyle(.secondary)
            ForEach(session.pendingLogouts) { item in
                VStack(alignment: .leading, spacing: 6) {
                    Text(item.name).font(.subheadline.bold())
                    Text(item.address).font(.footnote).foregroundStyle(.secondary)
                    Button("重试撤销远端授权") { session.retryLogout(item.id) }
                        .buttonStyle(.bordered).controlSize(.large)
                        .disabled(session.busy || session.isRevoking(item.id)).accessibilityIdentifier("retry-native-logout")
                }
            }
            Text("待撤销凭据仅暂存在本次 App 进程内，不用于恢复登录。关闭 App 后如仍未确认，请在 Desktop 的设备授权中撤销。")
                .font(.footnote).foregroundStyle(.secondary)
        }
    }
}
