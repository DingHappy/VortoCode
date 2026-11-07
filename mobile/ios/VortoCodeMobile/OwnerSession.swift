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
    var dispatches: [DispatchProposal] = []
    var changes: [OwnerChange] = []
    var timeline: ActivityTimeline?
    var timelineError = ""
    private var timelineRequest = UUID()
    private var timelineEpoch = UUID()

    func resetTimeline() {
        polling?.cancel()
        turn = nil
        chat = []
        chatNeedsReview = false
        chatRequest = Self.newRequestId()
        timelineEpoch = UUID()
        timelineRequest = UUID()
        timeline = nil
        timelineError = ""
    }
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
    var chatNeedsReview = false
    private var polling: Task<Void, Never>?

    private static func newRequestId() -> String { UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased() }

    func prepareReviewedRequest() {
        guard chatNeedsReview else { return }
        chatRequest = Self.newRequestId()
        chatNeedsReview = false
        note = "已准备新请求。请核对输入后手动发送；旧审批和执行不会恢复。"
    }

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
            async let orders = api(entry).dispatches(credential)
            decisions = try await pending
            changes = Array(try await done.reversed())
            dispatches = try await orders
        } catch { say(error) }
        await refreshTimeline(entry, credential)
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

    func decideDispatch(_ entry: AssistantEntry, _ credential: NativeCredential, _ proposal: DispatchProposal,
                        approve: Bool) async {
        guard let device = bootstrap?.device.id, !working else { return }
        working = true; defer { working = false }
        do {
            let outcome = try await api(entry).dispatch(credential, signer: signer, deviceId: device,
                                                        proposal: proposal, approve: approve)
            note = "「\(proposal.title)」\(outcome.statusText)"
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

    /// 新请求和登录切换使旧响应失效；单独的错误提示不把读取失败当成没有动静。
    func refreshTimeline(_ entry: AssistantEntry, _ credential: NativeCredential) async {
        guard canWrite, !Task.isCancelled else { return }
        let request = UUID()
        timelineRequest = request
        do {
            let value = try await api(entry).timeline(credential)
            guard timelineRequest == request, !Task.isCancelled else { return }
            timeline = value
            timelineError = ""
        } catch {
            guard timelineRequest == request, !Task.isCancelled else { return }
            timelineError = "时间线暂时读不到；已有内容可能过期，请下拉刷新。"
            if error as? MobileError == .unauthorized { sessionExpired?() }
        }
    }

    func actOnTimeline(_ entry: AssistantEntry, _ credential: NativeCredential,
                       _ action: ActivityTimelineItem.Action) async {
        guard canWrite, let device = bootstrap?.device.id, !working else { return }
        working = true; defer { working = false }
        let epoch = timelineEpoch
        do {
            if action.type == "undo" {
                let result = try await api(entry).undoChange(credential, signer: signer, deviceId: device, operationId: action.id)
                guard epoch == timelineEpoch, !Task.isCancelled else { return }
                note = ([result.detail] + result.notRecallable).joined(separator: "；")
            } else if action.type == "resume_goal" {
                try await api(entry).resumeGoalAdvance(credential, signer: signer, deviceId: device, goalId: action.id)
                guard epoch == timelineEpoch, !Task.isCancelled else { return }
                note = "已恢复自动推进，助理会在后续巡检中继续处理。"
            } else { return }
        } catch {
            guard epoch == timelineEpoch, !Task.isCancelled else { return }
            say(error)
        }
        await refresh(entry, credential)
    }

    func loadChat(_ entry: AssistantEntry, _ credential: NativeCredential) async {
        guard canWrite else { return }
        let epoch = timelineEpoch
        do {
            let history = try await api(entry).chatHistory(credential)
            guard epoch == timelineEpoch, !Task.isCancelled else { return }
            chat = history.items
            chatRegistered = history.registered
        } catch {
            guard epoch == timelineEpoch, !Task.isCancelled else { return }
            say(error)
        }
    }

    /// 发一句话给助理；打开「只记下来」时改为随手记。重试复用同一个请求 id，服务端只起一个回合。
    func send(_ entry: AssistantEntry, _ credential: NativeCredential) async {
        if captureOnly { await capture(entry, credential); await loadChat(entry, credential); return }
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let device = bootstrap?.device.id, !sending, !chatNeedsReview, !text.isEmpty, turn?.isFinished ?? true else { return }
        let epoch = timelineEpoch
        sending = true; defer { sending = false }
        do {
            let started = try await api(entry).chatSend(credential, signer: signer, deviceId: device,
                                                        text: String(text.prefix(4000)), requestId: chatRequest)
            guard epoch == timelineEpoch, !Task.isCancelled else { return }
            chat.append(ChatItem(role: "user", text: text, rid: started.rid, source: "app"))
            turn = started
            draft = ""
            chatRequest = Self.newRequestId()
            note = ""
            follow(entry, credential)
        } catch {
            guard epoch == timelineEpoch, !Task.isCancelled else { return }
            if case OwnerWriteError.requestNeedsReview = error { chatNeedsReview = true }
            say(error)
        }
    }

    /// 回合没结束就每 2 秒看一次；结束后重读对话（回复已写进主要聊天）。App 退到后台时停。
    func follow(_ entry: AssistantEntry, _ credential: NativeCredential) {
        polling?.cancel()
        guard let rid = turn?.rid, !(turn?.isFinished ?? true) else { return }
        let epoch = timelineEpoch
        polling = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(2))
                guard let self, !Task.isCancelled else { return }
                do {
                    let latest = try await self.api(entry).chatTurn(credential, rid: rid)
                    guard !Task.isCancelled, epoch == self.timelineEpoch, self.turn?.rid == rid else { return }
                    self.turn = latest
                    if latest.isFinished {
                        if latest.state != "done", !latest.error.isEmpty { self.note = latest.error }
                        await self.loadChat(entry, credential)
                        return
                    }
                } catch {
                    guard !Task.isCancelled, epoch == self.timelineEpoch, self.turn?.rid == rid else { return }
                    self.say(error)
                    return
                }
            }
        }
    }

    func stopFollowing() { polling?.cancel() }

    func decideCard(_ entry: AssistantEntry, _ credential: NativeCredential, _ card: ChatCard, approve: Bool) async {
        guard let device = bootstrap?.device.id, let rid = turn?.rid, !working else { return }
        let epoch = timelineEpoch
        working = true; defer { working = false }
        do {
            _ = try await api(entry).chatDecide(credential, signer: signer, deviceId: device, rid: rid,
                                                card: card, approve: approve)
            guard epoch == timelineEpoch, turn?.rid == rid, !Task.isCancelled else { return }
            let latest = try await api(entry).chatTurn(credential, rid: rid)
            guard epoch == timelineEpoch, turn?.rid == rid, !Task.isCancelled else { return }
            turn = latest
            if latest.outcome == "unknown" { note = latest.error }
            follow(entry, credential)
        } catch {
            guard epoch == timelineEpoch, turn?.rid == rid, !Task.isCancelled else { return }
            say(error)
        }
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
