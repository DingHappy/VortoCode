import SwiftUI
import MobileCore

struct PendingLogoutSummary: Identifiable {
    let id: UUID
    let name: String
    let address: String
}

@MainActor @Observable
final class AssistantSession {
    enum Phase {
        case idle, authenticating, reading, revoking
        var label: String {
            switch self {
            case .idle: return ""
            case .authenticating: return "正在登录助手…"
            case .reading: return "正在核对助手…"
            case .revoking: return "正在撤销远端授权…"
            }
        }
    }
    private struct PendingLogout {
        let id = UUID()
        let entry: AssistantEntry
        let credential: NativeCredential
    }
    var credential: NativeCredential?
    var reading = MobileReadState()
    private(set) var phase = Phase.idle
    var busy: Bool { phase != .idle }
    var note = ""
    var pendingLogouts: [PendingLogoutSummary] {
        pending.map { PendingLogoutSummary(id: $0.id, name: $0.credential.name, address: $0.entry.displayAddress) }
    }
    private var pending: [PendingLogout] = []
    private var revokingIDs: Set<UUID> = []
    private var generation = 0
    private var work: Task<Void, Never>?
    private let services: AssistantServices

    init(services: AssistantServices? = nil) { self.services = services ?? .live() }
    func stop() {
        generation += 1
        work?.cancel(); work = nil
        services.cancelAuthentication()
        phase = .idle
    }
    func restore(_ entry: AssistantEntry) {
        stop(); credential = nil; reading = MobileReadState(); note = ""
        do {
            if let saved = try services.load(), saved.origin == entry.displayAddress {
                try saved.credential.validate()
                credential = saved.credential
                refresh(entry)
            }
        } catch {
            if error as? MobileError == .unauthorized {
                do { try services.clear(); note = "上次会话已到期，请重新登录。" }
                catch { note = MobileError.secureStorage.localizedDescription }
            } else { note = MobileError.secureStorage.localizedDescription }
        }
    }
    func login(_ entry: AssistantEntry) {
        guard !busy else { return }
        stop(); reading = MobileReadState(); credential = nil; phase = .authenticating; note = ""
        let owner = generation
        work = Task {
            do {
                let value = try await services.authenticate(entry)
                guard owner == generation, !Task.isCancelled else { discardUnused(value, at: entry); return }
                do { try services.save(SavedSession(origin: entry.displayAddress, credential: value)) }
                catch { discardUnused(value, at: entry); throw MobileError.secureStorage }
                credential = value
                phase = .idle
                refresh(entry)
            } catch {
                guard owner == generation else { return }
                phase = .idle
                if error as? MobileError != .cancelled && !Task.isCancelled { note = error.localizedDescription }
            }
        }
    }
    func refresh(_ entry: AssistantEntry) {
        guard !busy, let credential else { return }
        let owner = generation
        phase = .reading; note = ""
        work = Task {
            do {
                let value = try await services.read(entry, credential)
                guard owner == generation, !Task.isCancelled else { return }
                try reading.accept(value, instance: credential.instanceId)
                phase = .idle
            } catch {
                guard owner == generation else { return }
                phase = .idle
                reading.markHistorical()
                if error as? MobileError == .unauthorized {
                    self.credential = nil; reading = MobileReadState()
                    do { try services.clear() }
                    catch { note = "账号授权已失效；本机安全存储暂时无法清除，请解锁设备后重试。"; return }
                }
                if !Task.isCancelled { note = error.localizedDescription }
            }
        }
    }
    func refreshAndWait(_ entry: AssistantEntry) async { refresh(entry); await work?.value }
    func suspendReads() {
        guard credential != nil else { return }
        reading.markHistorical()
        if phase != .revoking { stop() }
    }
    func logout(_ entry: AssistantEntry) {
        guard phase != .revoking else { return }
        stop(); note = ""
        do { try services.clear() }
        catch {
            reading.markHistorical()
            note = "无法清除本机安全存储，请解锁设备后重试退出。"
            return
        }
        let previous = credential
        credential = nil; reading = MobileReadState()
        if let previous { retryLogout(remember(previous, at: entry).id) }
    }
    func retryLogout(_ id: UUID) {
        guard !busy, !isRevoking(id), let item = pending.first(where: { $0.id == id }) else { return }
        stop(); phase = .revoking; note = ""
        revokingIDs.insert(id)
        let owner = generation
        work = Task {
            let confirmed = await revoke(item)
            guard owner == generation else { return }
            phase = .idle
            note = confirmed ? "已确认这次远端会话撤销或失效。" : "远端撤销尚未确认，可重试或在 Desktop 的设备授权中处理。"
        }
    }
    func isRevoking(_ id: UUID) -> Bool { revokingIDs.contains(id) }
    private func remember(_ credential: NativeCredential, at entry: AssistantEntry) -> PendingLogout {
        if let item = pending.first(where: { $0.entry == entry && $0.credential.token == credential.token }) { return item }
        let item = PendingLogout(entry: entry, credential: credential)
        pending.append(item)
        return item
    }
    private func revoke(_ item: PendingLogout) async -> Bool {
        defer { revokingIDs.remove(item.id) }
        do { try await services.revoke(item.entry, item.credential) }
        catch { if error as? MobileError != .unauthorized { return false } }
        pending.removeAll { $0.id == item.id }
        return true
    }
    private func discardUnused(_ value: NativeCredential, at entry: AssistantEntry) {
        let item = remember(value, at: entry)
        guard !isRevoking(item.id) else { return }
        revokingIDs.insert(item.id)
        // This task does not inherit cancellation from the abandoned login.
        // A late reply can be revoked without restoring the local login.
        Task { _ = await revoke(item) }
    }
}
