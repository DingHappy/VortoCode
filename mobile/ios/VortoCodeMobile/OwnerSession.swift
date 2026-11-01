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

    var canWrite: Bool { bootstrap?.canWrite ?? false }

    private func api(_ entry: AssistantEntry) -> MobileAPI { MobileAPI(entry: entry) }

    private func say(_ error: Error) {
        if let error = error as? LocalizedError, let text = error.errorDescription { note = text }
        else { note = "这次操作没有完成，请重试。" }
    }

    /// 读 bootstrap；没开写入时试着绑定钥匙（服务端没开会回 403，就停在只读）。
    func load(_ entry: AssistantEntry, _ credential: NativeCredential) async {
        do {
            var boot = try await api(entry).bootstrap(credential)
            if !boot.canWrite {
                do {
                    try await api(entry).bindKeys(credential, deviceKey: try DeviceKeys.publicKey(.device),
                                                  approvalKey: try DeviceKeys.publicKey(.approval))
                    boot = try await api(entry).bootstrap(credential)
                } catch OwnerWriteError.writesDisabled {
                    // 服务端还没开启手机写入：保持只读，不提示错误
                }
            }
            bootstrap = boot
            if boot.canWrite { await refresh(entry, credential); await registerPush(entry, credential) }
        } catch { say(error) }
    }

    func refresh(_ entry: AssistantEntry, _ credential: NativeCredential) async {
        guard canWrite else { return }
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
