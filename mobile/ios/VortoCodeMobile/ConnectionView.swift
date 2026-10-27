import SwiftUI

struct ConnectionView: View {
    @Binding var savedOrigin: String
    @Environment(\.dismiss) private var dismiss
    @State private var address = ""
    @State private var error: String?
    @State private var confirmForget = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack {
                        TextField("https://assistant.example.com", text: $address)
                            .textContentType(.URL).keyboardType(.URL).textInputAutocapitalization(.never)
                            .autocorrectionDisabled().accessibilityLabel("助手入口地址")
                            .accessibilityIdentifier("entry-address")
                            .onChange(of: address) { _, _ in error = nil }
                        Button { address = "" } label: { Image(systemName: "xmark.circle.fill").frame(width: 44, height: 44) }
                            .buttonStyle(.plain).disabled(address.isEmpty)
                            .accessibilityLabel("清除地址").accessibilityIdentifier("clear-entry-address")
                    }
                    if let error { Text(error).foregroundStyle(.red).accessibilityIdentifier("entry-error") }
                    Button("保存入口") { save() }.accessibilityIdentifier("save-entry")
                } header: { Text("常驻助手") } footer: {
                    Text("从 Desktop 的“账号与用量 → 手机与常驻助手”复制手机入口。这里只保存地址，不填写账号密码或管理员令牌。")
                }
                Section("本机与账号") {
                    Label("账号在系统浏览页面登录", systemImage: "person.crop.circle")
                    Text("这台手机不执行后台任务。任务、目标和定时记录仍在原常驻服务器。")
                    Text("登录后在 App 的“设置”里退出手机账号；退出会清除本机安全会话并请求服务器撤销。系统浏览器的账号登录单独保留。")
                }.font(.subheadline)
                if !savedOrigin.isEmpty {
                    Section {
                        Button("忘记这台助手", role: .destructive) { confirmForget = true }
                            .accessibilityIdentifier("forget-entry")
                    } footer: { Text("只删除此 App 保存的入口地址，不修改服务器，也不清除系统浏览会话。") }
                }
                #if DEBUG && targetEnvironment(simulator)
                Section { Text("开发模拟器允许 HTTP 回环验收；正式版本及真实手机仍只允许 HTTPS。") }
                    .font(.footnote).foregroundStyle(.secondary)
                #endif
            }
            .navigationTitle("连接设置").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() } } }
            .onAppear { address = savedOrigin }
            .confirmationDialog("忘记此入口？已有账号会话仍保留。", isPresented: $confirmForget, titleVisibility: .visible) {
                Button("忘记入口", role: .destructive) { savedOrigin = ""; dismiss() }
            }
        }
    }

    private func save() {
        do {
            let entry = try EntryPolicy.parse(address)
            savedOrigin = entry.displayAddress
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
