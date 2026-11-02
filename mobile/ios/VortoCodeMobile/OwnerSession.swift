import SwiftUI
import UIKit
import UserNotifications
import MobileCore

/// APNs 设备令牌由系统异步交给 AppDelegate；这里只转交，不持久化。
@MainActor @Observable
final class PushRegistry {
    static let shared = PushRegistry()
    var token: String?
    var failed = false
}

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }
    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let hex = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { @MainActor in PushRegistry.shared.token = hex }
    }
    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        Task { @MainActor in PushRegistry.shared.failed = true }
    }
    // 前台收到也照常展示（正文已由通知扩展解密）
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async
        -> UNNotificationPresentationOptions { [.banner, .sound, .list] }
}

/// 主人终端的写入：等你决定、随手记、我替你做了什么。与只读会话分开，写入失败不清除登录。
@MainActor @Observable
final class OwnerSession {
    var bootstrap: NativeBootstrap?
    var decisions: [PendingDecision] = []
    var changes: [OwnerChange] = []
    var note = ""
    var working = false
    /// 随手记的草稿和它的请求 id：提交成功前重试都用同一个 id，服务端只记一次
    var draft = ""
    private var draftRequest = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
    private var pushedToken: String?
    private let signer = DeviceSigner()

    // —— 对话（ADR-012）：App 是主要聊天的又一个入口 ——
    var chat: [ChatItem] = []
    var chatRegistered = true
    var turn: ChatTurn?
    var sending = false
    /// 打开时输入框只记下来（随手记），不让助理去做
    var captureOnly = false
    private var chatRequest = OwnerSession.newRequestId()
    private var polling: Task<Void, Never>?

    private static func newRequestId() -> String { UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased() }

    var canWrite: Bool { bootstrap?.canWrite ?? false }
    /// 这台手机的钥匙在等 Desktop 批准时的核对码
    var approvalCode: String?
    /// 不能写入时告诉你为什么
    var readOnlyReason: String {
        if let approvalCode { return "这台手机在等你在 Desktop 上确认（核对码 \(approvalCode)）。对上核对码批准后，回到这里下拉刷新。" }
        return "这台助理还没开启手机写入，这里只显示说明。"
    }

    private func api(_ entry: AssistantEntry) -> MobileAPI { MobileAPI(entry: entry) }

    /// 写入这边发现登录已失效（如网关重启后会话没恢复）：交给只读会话去核对并回到登录页，
    /// 不在各页停着显示「登录或设备授权已失效」
    var sessionExpired: (() -> Void)?

    private func say(_ error: Error) {
        if error as? MobileError == .unauthorized { sessionExpired?(); return }
        if let error = error as? LocalizedError, let text = error.errorDescription { note = text }
        else { note = "这次操作没有完成，请重试。" }
    }

    /// 读 bootstrap；没开写入时试着绑定钥匙（服务端没开会回 403，就停在只读）。
    func load(_ entry: AssistantEntry, _ credential: NativeCredential) async {
        do {
            var boot = try await api(entry).bootstrap(credential)
            if !boot.canWrite {
                do {
                    let binding = try await api(entry).bindKeys(credential, deviceKey: try DeviceKeys.publicKey(.device),
                                                                approvalKey: try DeviceKeys.publicKey(.approval))
                    approvalCode = binding.waitingApproval ? binding.code : nil
                    boot = try await api(entry).bootstrap(credential)
                } catch OwnerWriteError.writesDisabled {
                    // 服务端还没开启手机写入：保持只读，不提示错误
                }
            }
            bootstrap = boot
            if boot.canWrite {
                await refresh(entry, credential)
                await loadChat(entry, credential)
                await registerPush(entry, credential)
            }
        } catch { say(error) }
    }

    func refresh(_ entry: AssistantEntry, _ credential: NativeCredential) async {
        // 还不能写（比如钥匙刚在 Desktop 上批准）：下拉刷新时重新走一遍绑定
        guard canWrite else { await load(entry, credential); return }
        do {
            async let pending = api(entry).decisions(credential)
            async let done = api(entry).changes(credential)
            decisions = try await pending
            changes = Array(try await done.reversed())
        } catch { say(error) }
    }

    func decide(_ entry: AssistantEntry, _ credential: NativeCredential, _ decision: PendingDecision, approve: Bool) async {
        guard let device = bootstrap?.device.id, !working else { return }
        working = true; defer { working = false }
        do {
            let outcome = try await api(entry).decide(credential, signer: signer, deviceId: device,
                                                      decision: decision, approve: approve)
            note = approve ? "已批准：\(outcome.statusText)" : "已拒绝，这条不会发出。"
        } catch { say(error) }
        await refresh(entry, credential)
    }

    func capture(_ entry: AssistantEntry, _ credential: NativeCredential) async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let device = bootstrap?.device.id, !working, !text.isEmpty else { return }
        working = true; defer { working = false }
        do {
            let outcome = try await api(entry).capture(credential, signer: signer, deviceId: device,
                                                       text: String(text.prefix(500)), requestId: draftRequest)
            note = "记下了：\(outcome.objective)"
            draft = ""
            draftRequest = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        } catch { say(error) }
        await refresh(entry, credential)
    }

    func undo(_ entry: AssistantEntry, _ credential: NativeCredential, _ change: OwnerChange) async {
        guard let device = bootstrap?.device.id, !working else { return }
        working = true; defer { working = false }
        do {
            let outcome = try await api(entry).undo(credential, signer: signer, deviceId: device, change: change)
            note = ([outcome.detail] + outcome.notRecallable).joined(separator: "；")
        } catch { say(error) }
        await refresh(entry, credential)
    }

    func loadChat(_ entry: AssistantEntry, _ credential: NativeCredential) async {
        guard canWrite else { return }
        do {
            let history = try await api(entry).chatHistory(credential)
            chat = history.items
            chatRegistered = history.registered
        } catch { say(error) }
    }

    /// 发一句话给助理；打开「只记下来」时改为随手记。重试复用同一个请求 id，服务端只起一个回合。
    func send(_ entry: AssistantEntry, _ credential: NativeCredential) async {
        if captureOnly { await capture(entry, credential); await loadChat(entry, credential); return }
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let device = bootstrap?.device.id, !sending, !text.isEmpty, turn?.isFinished ?? true else { return }
        sending = true; defer { sending = false }
        do {
            let started = try await api(entry).chatSend(credential, signer: signer, deviceId: device,
                                                        text: String(text.prefix(4000)), requestId: chatRequest)
            chat.append(ChatItem(role: "user", text: text, rid: started.rid, source: "app"))
            turn = started
            draft = ""
            chatRequest = Self.newRequestId()
            note = ""
            follow(entry, credential)
        } catch { say(error) }
    }

    /// 回合没结束就每 2 秒看一次；结束后重读对话（回复已写进主要聊天）。App 退到后台时停。
    func follow(_ entry: AssistantEntry, _ credential: NativeCredential) {
        polling?.cancel()
        guard let rid = turn?.rid, !(turn?.isFinished ?? true) else { return }
        polling = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(2))
                guard let self, !Task.isCancelled else { return }
                do {
                    let latest = try await self.api(entry).chatTurn(credential, rid: rid)
                    self.turn = latest
                    if latest.isFinished {
                        if latest.state != "done", !latest.error.isEmpty { self.note = latest.error }
                        await self.loadChat(entry, credential)
                        return
                    }
                } catch {
                    self.say(error)
                    return
                }
            }
        }
    }

    func stopFollowing() { polling?.cancel() }

    func decideCard(_ entry: AssistantEntry, _ credential: NativeCredential, _ card: ChatCard, approve: Bool) async {
        guard let device = bootstrap?.device.id, let rid = turn?.rid, !working else { return }
        working = true; defer { working = false }
        do {
            _ = try await api(entry).chatDecide(credential, signer: signer, deviceId: device, rid: rid,
                                                card: card, approve: approve)
            turn = try await api(entry).chatTurn(credential, rid: rid)
            follow(entry, credential)
        } catch { say(error) }
    }

    /// 申请通知权限并登记推送；每次启动都重新登记（令牌可能变化），失败不影响其它功能。
    func registerPush(_ entry: AssistantEntry, _ credential: NativeCredential) async {
        guard let device = bootstrap?.device.id else { return }
        let granted = (try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])) ?? false
        guard granted else { return }
        UIApplication.shared.registerForRemoteNotifications()
        for _ in 0..<20 where PushRegistry.shared.token == nil && !PushRegistry.shared.failed {
            try? await Task.sleep(for: .milliseconds(250))
        }
        guard let token = PushRegistry.shared.token, token != pushedToken else { return }
        #if DEBUG
        let environment = "sandbox"
        #else
        let environment = "production"
        #endif
        do {
            try await api(entry).registerPush(credential, signer: signer, deviceId: device, apnsToken: token,
                                              pushKey: try DeviceKeys.pushPublicKey(), environment: environment)
            pushedToken = token
        } catch { say(error) }
    }
}
